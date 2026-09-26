"""Claude adapter over the official ``anthropic`` SDK (ADR 0002, ADR 0004)."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any, cast

import anthropic
from anthropic.types import Message as AnthropicMessage
from anthropic.types import MessageParam, TextBlock, ToolUseBlock
from anthropic.types import Usage as AnthropicUsage

from dowbench.metering.usage import Usage
from dowbench.providers.base import (
    Message,
    NativeContent,
    Request,
    Response,
    StopReason,
    ToolCall,
    UsageMappingError,
)

PROVIDER = "anthropic"

# Every field of anthropic.types.Usage this adapter knows about. A test fails when the SDK
# adds one, so a new billing dimension cannot be silently ignored.
USAGE_FIELDS = frozenset(
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

_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def usage_from_anthropic(usage: AnthropicUsage) -> Usage:
    unknown = sorted(k for k, v in (usage.model_extra or {}).items() if v is not None)
    if unknown:
        raise UsageMappingError(f"unmapped Anthropic usage fields: {unknown}")
    server = usage.server_tool_use
    if server is not None and (server.web_search_requests or server.web_fetch_requests):
        raise UsageMappingError("server tool requests are not priced by dowbench")
    if usage.cache_creation is not None and usage.cache_creation.ephemeral_1h_input_tokens:
        raise UsageMappingError("1-hour cache writes are not priced by dowbench")
    # output_tokens is the inclusive billed total; thinking_tokens is an approximate subset
    # of it, so the split keeps the billed sum exact.
    details = usage.output_tokens_details
    thinking = min(details.thinking_tokens, usage.output_tokens) if details else 0
    return Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens - thinking,
        reasoning_tokens=thinking,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
    )


def to_anthropic_messages(messages: Sequence[Message]) -> list[MessageParam]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            out.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            if message.native is not None and message.native.provider == PROVIDER:
                content = list(message.native.content)
            else:
                content = [{"type": "text", "text": message.content}] if message.content else []
                content += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in message.tool_calls
                ]
            out.append({"role": "assistant", "content": content})
        else:
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            # All results of one assistant turn go back in a single user message.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return cast(list[MessageParam], out)


def response_from_anthropic(message: AnthropicMessage, *, latency_s: float) -> Response:
    text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
    calls = [
        ToolCall(id=b.id, name=b.name, arguments=cast(dict[str, Any], b.input))
        for b in message.content
        if isinstance(b, ToolUseBlock)
    ]
    return Response(
        text=text,
        tool_calls=calls,
        stop_reason=_STOP_REASONS.get(message.stop_reason or "", "other"),
        usage=usage_from_anthropic(message.usage),
        latency_s=latency_s,
        raw={"id": message.id, "model": message.model, "usage": message.usage.to_dict()},
        native=NativeContent(
            provider=PROVIDER,
            content=[b.to_dict(mode="json", exclude_none=True) for b in message.content],
        ),
    )


class AnthropicProvider:
    name = PROVIDER
    simulated = False

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        # Credentials come from the environment (ANTHROPIC_API_KEY); never pass them in code.
        self._client = client if client is not None else anthropic.Anthropic()

    def _params(self, request: Request) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": request.model,
            "messages": to_anthropic_messages(request.messages),
        }
        if request.system:
            params["system"] = request.system
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
        return params

    def count_tokens(self, request: Request) -> int:
        return self._client.messages.count_tokens(**self._params(request)).input_tokens

    def complete(self, request: Request) -> Response:
        start = time.perf_counter()
        message = self._client.messages.create(
            max_tokens=request.max_tokens, **self._params(request)
        )
        return response_from_anthropic(message, latency_s=time.perf_counter() - start)
