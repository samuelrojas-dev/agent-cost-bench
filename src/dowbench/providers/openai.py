"""OpenAI Chat Completions adapter on the official ``openai`` SDK (ADR 0002, ADR 0023).

The key is read only from ``OPENAI_API_KEY`` and handed to the SDK explicitly with the official
base URL, so no profile or ``OPENAI_BASE_URL`` can redirect the key or bill another account
(ADR 0005). The SDK is imported lazily so this module loads without it — the offline estimate
reads ``build_openai.counts_tokens`` without needing the SDK installed.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol, cast

from dowbench.metering.usage import Usage
from dowbench.providers.base import (
    Message,
    ProviderSetupError,
    Request,
    Response,
    StopReason,
    ToolCall,
    UsageMappingError,
    transient_boundary,
)

API_URL = "https://api.openai.com/v1"

# Top-level usage keys we know how to price; a new non-empty one fails loudly (ADR 0007).
_KNOWN_USAGE = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_tokens_details",
        "completion_tokens_details",
    }
)
_KNOWN_PROMPT_DETAILS = frozenset({"cached_tokens", "audio_tokens"})
_KNOWN_COMPLETION_DETAILS = frozenset(
    {"reasoning_tokens", "audio_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens"}
)

_STOP_REASONS: dict[str | None, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class _Completions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _Chat(Protocol):
    @property
    def completions(self) -> _Completions: ...


class _Client(Protocol):
    @property
    def chat(self) -> _Chat: ...


def _present(details: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (details or {}).items() if v not in (None, 0, [], {})}


def map_usage(usage: dict[str, Any]) -> Usage:
    """Map an OpenAI ``usage`` dict to disjoint counters (ADR 0023).

    Operates on the plain dict (``usage.model_dump()``) so it needs no SDK types and is tested
    with fixtures. Anything unpriceable — an unknown key, audio tokens, predicted-output tokens,
    or an inconsistent total — fails instead of being mispriced (ADR 0007).
    """
    unknown = {k for k, v in usage.items() if v not in (None, 0, {}, [])} - _KNOWN_USAGE
    if unknown:
        raise UsageMappingError(f"unknown OpenAI usage fields: {sorted(unknown)}")

    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = usage.get("total_tokens")
    if total is not None and int(total) != prompt + completion:
        raise UsageMappingError(
            f"total_tokens {total} != prompt {prompt} + completion {completion}"
        )

    pd = usage.get("prompt_tokens_details") or {}
    unknown_pd = set(_present(pd)) - _KNOWN_PROMPT_DETAILS
    if unknown_pd:
        raise UsageMappingError(f"unknown OpenAI prompt_tokens_details: {sorted(unknown_pd)}")
    if pd.get("audio_tokens"):
        raise UsageMappingError("audio input tokens are not priced")
    cached = int(pd.get("cached_tokens") or 0)
    if cached > prompt:
        raise UsageMappingError(f"cached {cached} exceeds prompt {prompt}")

    cd = usage.get("completion_tokens_details") or {}
    unknown_cd = set(_present(cd)) - _KNOWN_COMPLETION_DETAILS
    if unknown_cd:
        raise UsageMappingError(f"unknown OpenAI completion_tokens_details: {sorted(unknown_cd)}")
    if cd.get("audio_tokens"):
        raise UsageMappingError("audio output tokens are not priced")
    if cd.get("accepted_prediction_tokens") or cd.get("rejected_prediction_tokens"):
        raise UsageMappingError("predicted-output tokens are not priced")
    reasoning = int(cd.get("reasoning_tokens") or 0)
    if reasoning > completion:
        raise UsageMappingError(f"reasoning {reasoning} exceeds completion {completion}")

    return Usage(
        input_tokens=prompt - cached,
        cache_read_tokens=cached,
        cache_write_tokens=0,
        output_tokens=completion - reasoning,
        reasoning_tokens=reasoning,
    )


def to_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    """Map the neutral turns to OpenAI chat messages. The assistant turn is stored and replayed
    verbatim from ``provider_data`` so a recorded run reproduces (ADR 0015)."""
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "user":
            out.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            replay = message.provider_data.get("message")
            if replay is not None:
                out.append(replay)
                continue
            tool_calls = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in message.tool_calls
            ]
            turn: dict[str, Any] = {"role": "assistant", "content": message.content or None}
            if tool_calls:
                turn["tool_calls"] = tool_calls
            out.append(turn)
        else:
            if message.tool_call_id is None:
                raise ValueError("tool message without tool_call_id")
            out.append(
                {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
            )
    return out


def _tools(request: Request) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in request.tools
    ]


class OpenAIProvider:
    name = "openai"
    simulated = False
    # OpenAI has no server-side token count; the ceiling is enforced after each call (ADR 0023).
    counts_tokens = False

    def __init__(self, client: _Client | None = None) -> None:
        if client is None:
            try:
                import openai
            except ImportError as exc:
                raise ProviderSetupError(
                    "the openai provider needs its SDK: pip install 'dowbench[openai]'"
                ) from exc

            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise ProviderSetupError("OPENAI_API_KEY is not set; see .env.example")
            if os.environ.get("OPENAI_BASE_URL"):
                raise ProviderSetupError(
                    "OPENAI_BASE_URL is set; it could redirect the key to another endpoint. "
                    "Unset it for benchmark runs."
                )
            # No retries: a retried call may be billed twice and recorded once (ADR 0010).
            sdk = openai.OpenAI(api_key=api_key, base_url=API_URL, max_retries=0)
            client = cast(_Client, sdk)
        self._client = client

    def count_tokens(self, request: Request) -> int | None:
        """None: OpenAI cannot count input tokens before a call; the ceiling holds after it."""
        return None

    def complete(self, request: Request) -> Response:
        params: dict[str, Any] = {
            "model": request.model,
            "messages": to_messages(request.system, request.messages),
            "max_completion_tokens": request.max_tokens,
        }
        if request.tools:
            params["tools"] = _tools(request)
        start = time.perf_counter()
        with transient_boundary():
            completion = self._client.chat.completions.create(**params)
        latency = time.perf_counter() - start

        raw_usage = completion.usage.model_dump(mode="json", exclude_none=True)
        try:
            usage = map_usage(completion.usage.model_dump())
        except UsageMappingError as exc:
            raise UsageMappingError(str(exc), raw_usage) from exc

        choice = completion.choices[0]
        message = choice.message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=json.loads(tc.function.arguments))
            for tc in (message.tool_calls or [])
        ]
        return Response(
            text=message.content or "",
            tool_calls=tool_calls,
            stop_reason=_STOP_REASONS.get(choice.finish_reason, "other"),
            usage=usage,
            latency_s=latency,
            raw={
                "usage": raw_usage,
                "model": completion.model,
                "finish_reason": choice.finish_reason,
                "id": completion.id,
            },
            # Plain-dict assistant turn, replayed verbatim; JSON-safe, so no codec (ADR 0023).
            provider_data={"message": message.model_dump(mode="json", exclude_none=True)},
            model_version=completion.model,
            request_body=params,
        )


def build_openai(config: object, dataset: object) -> OpenAIProvider:
    """``dowbench.providers`` factory for the OpenAI adapter (ADR 0021)."""
    return OpenAIProvider()


# Read offline by the estimate without instantiating the provider or importing the SDK (ADR 0023).
build_openai.counts_tokens = False  # type: ignore[attr-defined]


def map_sut_response(response: object) -> tuple[Usage, dict[str, Any], str | None] | None:
    """``dowbench.sut_mappers`` entry: meter an OpenAI SDK completion for an agent run."""
    from openai.types.chat import ChatCompletion

    if isinstance(response, ChatCompletion):
        usage = response.usage
        if usage is None:
            return None
        raw = usage.model_dump(mode="json", exclude_none=True)
        try:
            return map_usage(usage.model_dump()), raw, response.model
        except UsageMappingError as exc:
            raise UsageMappingError(str(exc), raw) from exc
    return None
