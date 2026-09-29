"""Anthropic Messages API adapter on the official ``anthropic`` SDK (ADR 0002, ADR 0006).

The key is read only from ``ANTHROPIC_API_KEY`` and handed to the SDK explicitly, together
with the official base URL, so no profile, token or ``ANTHROPIC_BASE_URL`` can redirect
the key or bill another account (ADR 0005, ADR 0006).
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol, cast

import anthropic
from anthropic.types import (
    ContentBlock,
    MessageParam,
    MessageTokensCount,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
)
from anthropic.types import Message as ApiMessage
from anthropic.types import Usage as ApiUsage
from pydantic import BaseModel

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

API_URL = "https://api.anthropic.com"


# A new, non-empty usage field fails loudly so an SDK or API change cannot skew costs.
KNOWN_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "output_tokens_details",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cache_creation",
        "server_tool_use",
        "service_tier",
        "inference_geo",
    }
)

_STOP_REASONS: dict[str | None, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "refusal": "refusal",
}


class _Messages(Protocol):
    def create(self, **kwargs: Any) -> ApiMessage: ...

    def count_tokens(self, **kwargs: Any) -> MessageTokensCount: ...


class _Client(Protocol):
    @property
    def messages(self) -> _Messages: ...


def map_usage(usage: ApiUsage) -> Usage:
    """Map Anthropic usage to disjoint counters.

    ``output_tokens`` is the billed total and includes thinking; ``thinking_tokens`` is a
    documented approximation, so the split is approximate but the sum is exact. Anything
    the price table cannot price (non-standard tier, US-only inference, 1-hour cache
    writes, server tools) fails instead of being mispriced (ADR 0006).
    """
    present = {k for k, v in usage.model_dump(exclude_none=True).items() if v not in ([], {})}
    unknown = present - KNOWN_USAGE_FIELDS
    if unknown:
        raise UsageMappingError(f"unknown Anthropic usage fields: {sorted(unknown)}")
    if usage.service_tier not in (None, "standard"):
        raise UsageMappingError(f"service tier {usage.service_tier!r} is not priced")
    if usage.inference_geo not in (None, "global"):
        raise UsageMappingError(f"inference_geo {usage.inference_geo!r} is not priced")
    if usage.server_tool_use and any(usage.server_tool_use.model_dump().values()):
        raise UsageMappingError("server tool use is billed per request and is not priced")

    cache_write = usage.cache_creation_input_tokens or 0
    if usage.cache_creation is not None:
        if usage.cache_creation.ephemeral_1h_input_tokens:
            raise UsageMappingError("1-hour cache writes are not priced")
        if usage.cache_creation.ephemeral_5m_input_tokens != cache_write:
            raise UsageMappingError(
                f"cache write breakdown {usage.cache_creation.ephemeral_5m_input_tokens} "
                f"differs from cache_creation_input_tokens {cache_write}"
            )

    details = usage.output_tokens_details
    thinking = details.thinking_tokens if details else 0
    if thinking > usage.output_tokens:
        raise UsageMappingError(f"thinking {thinking} exceeds output {usage.output_tokens}")
    return Usage(
        input_tokens=usage.input_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_write_tokens=cache_write,
        output_tokens=usage.output_tokens - thinking,
        reasoning_tokens=thinking,
    )


def to_messages(messages: list[Message]) -> list[MessageParam]:
    params: list[MessageParam] = []
    for message in messages:
        if message.role == "user":
            params.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            replay = message.provider_data.get("content")
            if replay is not None:
                # Verbatim, with thinking blocks: edits would invalidate them (ADR 0006).
                params.append({"role": "assistant", "content": replay})
                continue
            blocks: list[Any] = (
                [{"type": "text", "text": message.content}] if message.content else []
            )
            blocks += [
                ToolUseBlockParam(type="tool_use", id=c.id, name=c.name, input=c.arguments)
                for c in message.tool_calls
            ]
            params.append({"role": "assistant", "content": blocks})
        else:
            if message.tool_call_id is None:
                raise ValueError("tool message without tool_call_id")
            result = ToolResultBlockParam(
                type="tool_result", tool_use_id=message.tool_call_id, content=message.content
            )
            # Results of parallel calls go back together in one user turn.
            previous = params[-1] if params else None
            if previous and previous["role"] == "user" and isinstance(previous["content"], list):
                previous["content"].append(result)
            else:
                params.append({"role": "user", "content": [result]})
    return params


def _tools(request: Request) -> list[ToolParam]:
    return [
        ToolParam(name=t.name, description=t.description, input_schema=t.parameters)
        for t in request.tools
    ]


def _common(request: Request) -> dict[str, Any]:
    """Arguments shared by counting and sending, so the count matches the request."""
    params: dict[str, Any] = {"model": request.model, "messages": to_messages(request.messages)}
    if request.system:
        params["system"] = request.system
    if request.tools:
        params["tools"] = _tools(request)
    return params


def _jsonable(value: Any) -> Any:
    """Plain JSON for a request that may hold SDK blocks replayed verbatim."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


class AnthropicProvider:
    name = "anthropic"
    simulated = False

    def __init__(self, client: _Client | None = None) -> None:
        if client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not api_key:
                raise ProviderSetupError("ANTHROPIC_API_KEY is not set; see .env.example")
            if os.environ.get("ANTHROPIC_CUSTOM_HEADERS"):
                raise ProviderSetupError(
                    "ANTHROPIC_CUSTOM_HEADERS is set; it could enable betas that change the "
                    "model or its billing. Unset it for benchmark runs."
                )
            # No retries: a retried call may be billed twice and recorded once. The default
            # timeout is kept so the SDK refuses non-streaming requests too long to finish.
            sdk = anthropic.Anthropic(api_key=api_key, base_url=API_URL, max_retries=0)
            # The SDK's overloaded signatures are narrower than the protocol's **kwargs.
            client = cast(_Client, sdk)
        self._client = client

    def count_tokens(self, request: Request) -> int:
        """Anthropic's estimate of the input tokens; see ADR 0006 for its accuracy."""
        with transient_boundary():
            return self._client.messages.count_tokens(**_common(request)).input_tokens

    def complete(self, request: Request) -> Response:
        params = {"max_tokens": request.max_tokens, **_common(request)}
        start = time.perf_counter()
        with transient_boundary():
            message = self._client.messages.create(**params)
        latency = time.perf_counter() - start

        raw_usage = message.usage.model_dump(mode="json", exclude_none=True)
        try:
            usage = map_usage(message.usage)
        except UsageMappingError as exc:
            raise UsageMappingError(str(exc), raw_usage) from exc
        blocks: list[ContentBlock] = list(message.content)
        text = "".join(b.text for b in blocks if b.type == "text")
        tool_calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in blocks
            if b.type == "tool_use"
        ]
        return Response(
            text=text,
            tool_calls=tool_calls,
            stop_reason=_STOP_REASONS.get(message.stop_reason, "other"),
            usage=usage,
            latency_s=latency,
            raw={
                "usage": raw_usage,
                "model": message.model,
                "stop_reason": message.stop_reason,
                "id": message.id,
            },
            provider_data={"content": blocks},
            model_version=message.model,
            request_body=_jsonable(params),
        )
