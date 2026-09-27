"""Record real provider calls to a cassette and replay them offline (ADR 0015).

A real run always records every ``count_tokens`` and ``complete`` call, in order, to
``cassette.jsonl``: the sanitized neutral request, a digest of it, and the neutral response
(with the provider's opaque per-turn data base64-encoded, ADR 0006). ``replay_run`` re-runs
the same episodes from that cassette with no network call and no new spend, so CI, tests and
demos reproduce a real run's numbers offline.

Matching is **in recorded order within an episode**, because the loop is deterministic given
its responses. The digest is only a drift check: if the code now builds a different request
than was recorded, replay raises ``CassetteDriftError`` instead of returning a stale response
or, ever, calling a real provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from dowbench.attacks.schema import Dataset
from dowbench.metering.pricing import PriceTable
from dowbench.metering.usage import Usage
from dowbench.providers.base import Provider, Request, Response, StopReason, ToolCall
from dowbench.providers.provider_data import decode_provider_data, encode_provider_data
from dowbench.runner.store import RunStore, sanitize


class CassetteDriftError(RuntimeError):
    """The cassette does not describe this code: the replayed request drifted, a call kind
    differs, or the cassette ran out of recorded calls. Replay stops; no provider is called."""


def request_digest(request: Request) -> str:
    """A stable hash of the sanitized neutral request; excludes anything secret (ADR 0009).

    ``provider_data`` on messages is ``exclude=True``, so opaque per-turn bytes never enter
    the digest and it stays identical between the recording run and the replay.
    """
    body = sanitize(request.model_dump(mode="json"))
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecordedResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
    stop_reason: StopReason
    usage: Usage
    latency_s: float = 0.0
    raw: dict[str, Any] = {}
    model_version: str | None = None
    # provider_data (signatures/thinking) JSON-encoded then base64, so opaque bytes survive
    # the JSONL round-trip. It is sanitized before encoding, never after (ADR 0015 §5).
    provider_data_b64: str = ""

    @classmethod
    def capture(cls, response: Response, provider: str) -> RecordedResponse:
        pd = sanitize(encode_provider_data(provider, response.provider_data))
        blob = base64.b64encode(json.dumps(pd, sort_keys=True).encode("utf-8")).decode("ascii")
        return cls(
            text=response.text,
            tool_calls=list(response.tool_calls),
            stop_reason=response.stop_reason,
            usage=response.usage,
            latency_s=response.latency_s,
            raw=sanitize(response.raw),
            model_version=response.model_version,
            provider_data_b64=blob,
        )

    def to_response(self, provider: str, request: dict[str, Any]) -> Response:
        raw = json.loads(base64.b64decode(self.provider_data_b64)) if self.provider_data_b64 else {}
        return Response(
            text=self.text,
            tool_calls=list(self.tool_calls),
            stop_reason=self.stop_reason,
            usage=self.usage,
            latency_s=self.latency_s,
            raw=self.raw,
            model_version=self.model_version,
            provider_data=decode_provider_data(provider, raw),
            request_body=request,  # so replay's requests.jsonl matches the recorded run
        )


class Interaction(BaseModel):
    episode_id: str
    attempt: str
    turn: int
    kind: Literal["count", "complete"]
    provider: str
    request_digest: str
    request: dict[str, Any]  # sanitized neutral request, for auditing and a drift diff
    input_tokens: int | None = None  # count calls only
    response: RecordedResponse | None = None  # complete calls only


class RecordingProvider:
    """Wrap a real provider so every call is written to the cassette as it returns.

    Added outside the rate limiter so it records the response the run actually used, and only
    for real providers (the mock is simulated and never recorded, ADR 0015 §1).
    """

    replaying = False

    def __init__(self, inner: Provider, store: RunStore) -> None:
        self._inner = inner
        self._store = store
        self._episode_id = ""
        self._attempt = ""
        self._turn = 0

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def simulated(self) -> bool:
        return self._inner.simulated

    def begin_episode(self, episode_id: str, attempt: str) -> None:
        self._episode_id, self._attempt, self._turn = episode_id, attempt, 0

    def count_tokens(self, request: Request) -> int | None:
        tokens = self._inner.count_tokens(request)
        self._write("count", request, input_tokens=tokens)
        return tokens

    def complete(self, request: Request) -> Response:
        response = self._inner.complete(request)
        self._write("complete", request, response=response)
        self._turn += 1
        return response

    def _write(
        self,
        kind: Literal["count", "complete"],
        request: Request,
        *,
        input_tokens: int | None = None,
        response: Response | None = None,
    ) -> None:
        recorded = RecordedResponse.capture(response, self._inner.name) if response else None
        interaction = Interaction(
            episode_id=self._episode_id,
            attempt=self._attempt,
            turn=self._turn,
            kind=kind,
            provider=self._inner.name,
            request_digest=request_digest(request),
            request=sanitize(request.model_dump(mode="json")),
            input_tokens=input_tokens,
            response=recorded,
        )
        self._store.append_cassette(interaction.model_dump(mode="json"))


class ReplayProvider:
    """A provider that returns recorded responses in order and never touches the network."""

    replaying = True

    def __init__(
        self,
        by_episode: Mapping[str, list[Interaction]],
        provider: str,
        *,
        simulated: bool,
    ) -> None:
        self._by_episode = {ep: deque(items) for ep, items in by_episode.items()}
        self._provider = provider
        self._simulated = simulated
        self._current: deque[Interaction] | None = None
        self._episode_id = ""

    @property
    def name(self) -> str:
        return self._provider

    @property
    def simulated(self) -> bool:
        # Mirror the recorded run: a real cassette replays as real data (marked replayed,
        # not simulated); a mock cassette stays SIMULATED and is never taken for a result.
        return self._simulated

    def begin_episode(self, episode_id: str, attempt: str) -> None:
        self._episode_id = episode_id
        self._current = self._by_episode.get(episode_id)
        if self._current is None:
            raise CassetteDriftError(f"cassette has no recorded calls for episode {episode_id!r}")

    def count_tokens(self, request: Request) -> int | None:
        return self._next("count", request).input_tokens

    def complete(self, request: Request) -> Response:
        interaction = self._next("complete", request)
        assert interaction.response is not None  # a complete row always carries a response
        return interaction.response.to_response(self._provider, interaction.request)

    def _next(self, kind: Literal["count", "complete"], request: Request) -> Interaction:
        if not self._current:
            raise CassetteDriftError(
                f"episode {self._episode_id!r}: the loop asked for a {kind} call but the "
                "cassette is exhausted (request drift)"
            )
        interaction = self._current.popleft()
        if interaction.kind != kind:
            raise CassetteDriftError(
                f"episode {self._episode_id!r} turn {interaction.turn}: recorded a "
                f"{interaction.kind} call but the loop issued a {kind} call (request drift)"
            )
        if interaction.request_digest != request_digest(request):
            raise CassetteDriftError(
                f"episode {self._episode_id!r} turn {interaction.turn}: the request changed "
                "since it was recorded; regenerate the cassette (request drift)"
            )
        return interaction


def load_cassette(store: RunStore) -> dict[str, list[Interaction]]:
    """Read a run's cassette, keyed by episode, keeping only the attempt that completed.

    A run dir can hold more than one attempt of the same episode: an interrupted attempt
    leaves its calls in the cassette but never writes an episode row (ADR 0007). Grouping by
    ``episode_id`` alone would splice an orphan attempt's calls before the good one and make
    replay drift, so the cassette is matched by ``(episode_id, attempt)`` and only the attempt
    named in ``episodes.jsonl`` (the one that completed) is kept (ADR 0015).
    """
    rows = store.load_cassette()
    if not rows:
        raise FileNotFoundError(
            f"{store.cassette_path} has no recorded calls; only real runs write a cassette"
        )
    # episode -> the attempt that produced its episode row; orphan attempts are absent here.
    completed: dict[str, str] = {r.episode_id: r.attempt for r in store.load_episodes()}
    by_episode: dict[str, list[Interaction]] = defaultdict(list)
    for row in rows:
        interaction = Interaction.model_validate(row)
        if completed.get(interaction.episode_id) == interaction.attempt:
            by_episode[interaction.episode_id].append(interaction)
    if not by_episode:
        raise FileNotFoundError(
            f"{store.cassette_path} has no completed attempt to replay; every recorded attempt "
            "is orphaned (no episode row)"
        )
    return dict(by_episode)


def replay_run(source_dir: Path, *, out_dir: Path, prices: PriceTable, dataset: Dataset) -> Any:
    """Re-run the episodes recorded in ``source_dir`` from its cassette, into ``out_dir``.

    Reads the config from the recorded ``run.json`` and makes no network call. Returns the
    ``RunSummary``; the derived files are written under ``out_dir/<run_name>``.
    """
    from dowbench.runner.execute import RunInfo, execute  # local: execute imports this module

    store = RunStore(source_dir)
    if not store.run_path.exists():
        raise FileNotFoundError(f"{store.run_path} not found; replay needs a recorded run")
    info = RunInfo.model_validate_json(store.run_path.read_text(encoding="utf-8"))
    if info.config.agent is not None:
        raise NotImplementedError(
            "replay covers built-in-loop runs; agent runs meter through the agent, not a "
            "recorded provider (ADR 0012)"
        )
    provider = ReplayProvider(load_cassette(store), info.provider, simulated=info.simulated)
    return execute(
        info.config, dataset, provider=provider, prices=prices, out_dir=out_dir, resume=False
    )
