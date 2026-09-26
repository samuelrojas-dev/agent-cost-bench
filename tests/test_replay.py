"""Record → replay tests (ADR 0015). Fully offline: the network is blocked by conftest,
and replay has no provider to call. Cassettes here are synthetic (a non-simulated mock)."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import pytest

from dowbench.attacks.schema import load_dataset
from dowbench.metering.pricing import PriceTable
from dowbench.metering.usage import Usage
from dowbench.providers.base import Message, Request, Response, ToolCall
from dowbench.providers.mock import MockProvider
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import execute
from dowbench.runner.replay import (
    CassetteDriftError,
    Interaction,
    RecordedResponse,
    ReplayProvider,
    replay_run,
    request_digest,
)

PILOT = Path(__file__).parent.parent / "configs" / "pilot.yaml"
FIXTURE = Path(__file__).parent / "fixtures" / "cassette-demo"


class RealishProvider(MockProvider):
    """A deterministic, offline provider that reports itself as real, so runs record it."""

    simulated = False


def _config() -> RunConfig:
    data = RunConfig.from_yaml(PILOT).model_dump(mode="json")
    task = load_dataset().benign[0].id
    data.update(
        {"attacks": [], "benign_tasks": [task], "defenses": [{"name": "none"}], "repeats": 1}
    )
    return RunConfig.model_validate(data)


def _record(out: Path, provider: MockProvider | None = None) -> Path:
    config, dataset = _config(), load_dataset()
    execute(
        config,
        dataset,
        provider=provider or RealishProvider(),
        prices=PriceTable.load(),
        out_dir=out,
    )
    return out / config.run_name


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# --- recording ---


def test_real_run_writes_a_cassette_but_a_simulated_run_does_not(tmp_path: Path) -> None:
    real_dir = _record(tmp_path / "real")
    cassette = _rows(real_dir / "cassette.jsonl")
    assert cassette  # a real (non-simulated) provider records
    kinds = [r["kind"] for r in cassette]
    assert set(kinds) == {"count", "complete"} and kinds[0] == "count"

    # The mock (simulated) writes no cassette: only real runs are recorded (ADR 0015 §1).
    config, dataset = _config(), load_dataset()
    execute(
        config,
        dataset,
        provider=MockProvider(),
        prices=PriceTable.load(),
        out_dir=tmp_path / "mock",
    )
    assert not (tmp_path / "mock" / config.run_name / "cassette.jsonl").exists()


# --- the reproduction guarantee the user asked for ---


def test_replay_reproduces_the_summary_with_an_identical_hash(tmp_path: Path) -> None:
    recorded = _record(tmp_path / "rec")
    original = _sha(recorded / "summary.json")

    summary = replay_run(
        recorded, out_dir=tmp_path / "rep", prices=PriceTable.load(), dataset=load_dataset()
    )
    replayed_dir = tmp_path / "rep" / summary.run_name
    assert _sha(replayed_dir / "summary.json") == original
    # No cassette is written on replay: it reads one, it does not record another.
    assert not (replayed_dir / "cassette.jsonl").exists()
    assert summary.simulated is False  # replayed numbers are real recorded data, not simulated


def test_replay_reproduces_the_billed_usage_call_for_call(tmp_path: Path) -> None:
    recorded = _record(tmp_path / "rec")
    replay_run(recorded, out_dir=tmp_path / "rep", prices=PriceTable.load(), dataset=load_dataset())
    run_name = _config().run_name
    original = [c["usage"] for c in _rows(recorded / "calls.jsonl")]
    replayed = [c["usage"] for c in _rows(tmp_path / "rep" / run_name / "calls.jsonl")]
    assert replayed == original and original  # same calls, same usage, no new spend


def test_replay_marks_run_json_replayed_and_the_report_says_so(tmp_path: Path) -> None:
    from dowbench.report import REPLAYED_BANNER
    from dowbench.report import load as load_report
    from dowbench.report import render as render_report

    recorded = _record(tmp_path / "rec")
    assert json.loads((recorded / "run.json").read_text())["replayed"] is False

    summary = replay_run(
        recorded, out_dir=tmp_path / "rep", prices=PriceTable.load(), dataset=load_dataset()
    )
    replayed_dir = tmp_path / "rep" / summary.run_name
    info = json.loads((replayed_dir / "run.json").read_text())
    assert info["replayed"] is True and info["simulated"] is False  # real data, not a new result
    assert REPLAYED_BANNER in render_report(*load_report(replayed_dir))


# --- the drift guarantee: never a real call ---


def test_request_drift_raises_and_never_calls_a_provider(tmp_path: Path) -> None:
    recorded = _record(tmp_path / "rec")
    cassette_path = recorded / "cassette.jsonl"
    rows = _rows(cassette_path)
    # Corrupt the recorded digest of the first call: the replay's real request no longer matches.
    rows[0]["request_digest"] = "sha256:" + "0" * 64
    cassette_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    with pytest.raises(CassetteDriftError, match="drift"):
        replay_run(
            recorded, out_dir=tmp_path / "rep", prices=PriceTable.load(), dataset=load_dataset()
        )


def _interaction(kind: Literal["count", "complete"], request: Request, **extra: Any) -> Interaction:
    return Interaction(
        episode_id="ep",
        attempt="a",
        turn=0,
        kind=kind,
        provider="mock",
        request_digest=request_digest(request),
        request=request.model_dump(mode="json"),
        **extra,
    )


def _request(text: str) -> Request:
    return Request(
        model="m", system="s", messages=[Message(role="user", content=text)], max_tokens=8
    )


def test_replayprovider_is_offline_and_detects_every_drift_shape() -> None:
    good = _request("hi")
    resp = RecordedResponse(stop_reason="end_turn", usage=Usage(input_tokens=3))
    provider = ReplayProvider(
        {
            "ep": [
                _interaction("count", good, input_tokens=3),
                _interaction("complete", good, response=resp),
            ]
        },
        "mock",
    )
    provider.begin_episode("ep", "a")
    assert provider.name == "mock" and provider.simulated is False
    assert provider.count_tokens(good) == 3
    assert provider.complete(good).stop_reason == "end_turn"

    # A different request at replay time is drift, not a silent stale response.
    provider = ReplayProvider({"ep": [_interaction("count", good, input_tokens=3)]}, "mock")
    provider.begin_episode("ep", "a")
    with pytest.raises(CassetteDriftError, match="drift"):
        provider.count_tokens(_request("changed"))

    # An unknown episode, a wrong call kind, and an exhausted cassette are all drift.
    provider = ReplayProvider({"ep": []}, "mock")
    with pytest.raises(CassetteDriftError, match="no recorded calls"):
        provider.begin_episode("other", "a")
    provider = ReplayProvider({"ep": [_interaction("count", good, input_tokens=3)]}, "mock")
    provider.begin_episode("ep", "a")
    with pytest.raises(CassetteDriftError, match="drift"):
        provider.complete(good)  # a complete asked where a count was recorded


# --- provider_data survives the base64 round-trip ---


def test_provider_data_round_trips_through_the_cassette() -> None:
    response = Response(
        text="ok",
        tool_calls=[ToolCall(id="t1", name="fetch", arguments={"page": 1})],
        stop_reason="tool_use",
        usage=Usage(input_tokens=10, output_tokens=5),
        raw={"usage": {"input_tokens": 10}},
        provider_data={"content": {"parts": [{"text": "verbatim", "signature": "abc"}]}},
        model_version="m-001",
    )
    recorded = RecordedResponse.capture(response, "mock")
    assert recorded.provider_data_b64  # opaque per-turn data is kept, base64-encoded
    back = recorded.to_response("mock", request={"model": "m"})
    assert back.provider_data == response.provider_data
    assert back.tool_calls == response.tool_calls
    assert back.model_version == "m-001"


# --- no key ever reaches the cassette, even inside base64 provider_data ---


# Every key shape the sanitizer masks (ADR 0009): Google API keys, Google OAuth (AQ.)
# and Anthropic keys. All three must be scrubbed from the cassette, base64 included.
_KEY_SHAPES = ("AIza", "AQ.", "sk-ant-")


class _LeakyProvider(RealishProvider):
    """A provider that (wrongly) puts key-shaped strings in its response, to prove sanitize."""

    def complete(self, request: Request) -> Response:
        response = super().complete(request)
        return response.model_copy(
            update={
                "provider_data": {
                    "signature": "AIza" + "L" * 35,
                    "oauth": "AQ." + "O" * 40,  # Google OAuth token shape (AQ.)
                    "note": "sk-ant-" + "L" * 40,
                },
                "raw": {
                    "usage": response.usage.model_dump(),
                    "api_key": "AIza" + "Z" * 35,
                    "refresh_token": "AQ." + "R" * 40,
                },
            }
        )


def test_no_api_key_reaches_the_cassette_even_inside_provider_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "K" * 35)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "S" * 40)
    recorded = _record(tmp_path / "leak", provider=_LeakyProvider())
    cassette_path = recorded / "cassette.jsonl"
    text = cassette_path.read_text(encoding="utf-8")

    for shape in _KEY_SHAPES:
        assert shape not in text  # not in the plaintext of the file
    # And not hidden inside the base64 provider_data either: decode and check.
    for row in _rows(cassette_path):
        blob = (row.get("response") or {}).get("provider_data_b64", "")
        if blob:
            decoded = base64.b64decode(blob).decode("utf-8")
            for shape in _KEY_SHAPES:
                assert shape not in decoded


# --- a committed synthetic fixture replays offline (ADR 0015 §6) ---


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture not generated")
def test_committed_fixture_cassette_replays_to_its_recorded_summary(tmp_path: Path) -> None:
    summary = replay_run(
        FIXTURE, out_dir=tmp_path, prices=PriceTable.load(), dataset=load_dataset()
    )
    replayed = tmp_path / summary.run_name / "summary.json"
    assert _sha(replayed) == _sha(FIXTURE / "summary.json")
