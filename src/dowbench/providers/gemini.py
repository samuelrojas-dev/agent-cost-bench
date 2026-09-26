"""Gemini Developer API adapter on the official ``google-genai`` SDK (ADR 0002, ADR 0004).

The API key comes from ``GEMINI_API_KEY`` in the environment; it is never passed in code.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from google.genai import types

from dowbench.metering.usage import Usage
from dowbench.providers.base import Message, Request, Response, StopReason, ToolCall


class UsageMappingError(ValueError):
    """The provider's usage report does not match the mapping in ADR 0002."""


# Fields of GenerateContentResponseUsageMetadata this adapter understands. A new, non-empty
# field fails loudly so an SDK or API change cannot silently skew costs (ADR 0002).
KNOWN_USAGE_FIELDS = frozenset(
    {
        "prompt_token_count",
        "cached_content_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "tool_use_prompt_token_count",
        "total_token_count",
        "prompt_tokens_details",
        "cache_tokens_details",
        "candidates_tokens_details",
        "tool_use_prompt_tokens_details",
        "traffic_type",
    }
)

_REFUSALS = frozenset(
    {
        types.FinishReason.SAFETY,
        types.FinishReason.RECITATION,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.SPII,
    }
)

# Headroom for the chat template around system instruction and function declarations,
# which the Developer API cannot count (ADR 0004).
TEMPLATE_MARGIN_TOKENS = 64


class _Models(Protocol):
    def generate_content(
        self, *, model: str, contents: Any, config: Any
    ) -> types.GenerateContentResponse: ...

    def count_tokens(self, *, model: str, contents: Any) -> types.CountTokensResponse: ...


class _Client(Protocol):
    @property
    def models(self) -> _Models: ...


def map_usage(metadata: types.GenerateContentResponseUsageMetadata | None) -> Usage:
    """Map Gemini usage to disjoint counters.

    ``prompt_token_count`` includes cached tokens; thoughts and tool-use prompt tokens are
    reported apart from it and from ``candidates_token_count``.
    """
    if metadata is None:
        raise UsageMappingError("response has no usage_metadata")
    present = {k for k, v in metadata.model_dump(exclude_none=True).items() if v not in ([], {})}
    unknown = present - KNOWN_USAGE_FIELDS
    if unknown:
        raise UsageMappingError(f"unknown Gemini usage fields: {sorted(unknown)}")

    prompt = metadata.prompt_token_count or 0
    cached = metadata.cached_content_token_count or 0
    tool_prompt = metadata.tool_use_prompt_token_count or 0
    if cached > prompt:
        raise UsageMappingError(f"cached tokens {cached} exceed prompt tokens {prompt}")
    usage = Usage(
        input_tokens=prompt - cached + tool_prompt,
        cache_read_tokens=cached,
        output_tokens=metadata.candidates_token_count or 0,
        reasoning_tokens=metadata.thoughts_token_count or 0,
    )
    total = metadata.total_token_count
    if total is not None and total != usage.total_tokens:
        raise UsageMappingError(f"mapped {usage.total_tokens} tokens but Gemini reports {total}")
    return usage


def to_contents(messages: list[Message]) -> list[types.Content]:
    names = {call.id: call.name for m in messages for call in m.tool_calls}
    contents: list[types.Content] = []
    for message in messages:
        if message.role == "user":
            contents.append(types.Content(role="user", parts=[types.Part(text=message.content)]))
        elif message.role == "assistant":
            parts = [types.Part(text=message.content)] if message.content else []
            parts += [
                types.Part(
                    function_call=types.FunctionCall(
                        id=call.id, name=call.name, args=call.arguments
                    ),
                    thought_signature=call.provider_data.get("thought_signature"),
                )
                for call in message.tool_calls
            ]
            contents.append(types.Content(role="model", parts=parts))
        else:
            if message.tool_call_id not in names:
                raise ValueError(f"tool result for unknown call {message.tool_call_id!r}")
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=message.tool_call_id,
                    name=names[message.tool_call_id],
                    response={"result": message.content},
                )
            )
            # Results of parallel calls go back together in one user turn.
            previous = contents[-1] if contents else None
            if previous and previous.parts and previous.parts[-1].function_response:
                previous.parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
    return contents


def _tools(request: Request) -> list[types.Tool] | None:
    if not request.tools:
        return None
    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=spec.name,
                    description=spec.description,
                    parameters_json_schema=spec.parameters,
                )
                for spec in request.tools
            ]
        )
    ]


def _stop_reason(response: types.GenerateContentResponse, tool_calls: list[ToolCall]) -> StopReason:
    if tool_calls:
        return "tool_use"
    if not response.candidates:
        feedback = response.prompt_feedback
        return "refusal" if feedback and feedback.block_reason else "other"
    reason = response.candidates[0].finish_reason
    if reason == types.FinishReason.STOP:
        return "end_turn"
    if reason == types.FinishReason.MAX_TOKENS:
        return "max_tokens"
    if reason in _REFUSALS:
        return "refusal"
    return "other"


class GeminiProvider:
    name = "gemini"
    simulated = False

    def __init__(self, client: _Client | None = None) -> None:
        if client is None:
            from google import genai

            client = genai.Client()  # reads GEMINI_API_KEY
        self._client = client

    def count_tokens(self, request: Request) -> int:
        """Exact count of the messages plus an upper bound for system and tools (ADR 0004)."""
        contents = to_contents(request.messages)
        counted = self._client.models.count_tokens(model=request.model, contents=contents)
        tools_json = json.dumps([t.model_dump() for t in request.tools]) if request.tools else ""
        bound = len(request.system.encode()) + len(tools_json.encode()) + TEMPLATE_MARGIN_TOKENS
        return (counted.total_tokens or 0) + bound

    def complete(self, request: Request) -> Response:
        config = types.GenerateContentConfig(
            system_instruction=request.system or None,
            tools=_tools(request),
            max_output_tokens=request.max_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        start = time.perf_counter()
        response = self._client.models.generate_content(
            model=request.model, contents=to_contents(request.messages), config=config
        )
        latency = time.perf_counter() - start

        text: list[str] = []
        tool_calls: list[ToolCall] = []
        candidate = response.candidates[0] if response.candidates else None
        parts = candidate.content.parts if candidate and candidate.content else None
        for part in parts or []:
            if part.thought:
                continue
            if part.text:
                text.append(part.text)
            if part.function_call:
                call = part.function_call
                tool_calls.append(
                    ToolCall(
                        # Unique within the episode: tool results are matched by id.
                        id=call.id or f"call_{len(request.messages)}_{len(tool_calls)}",
                        name=call.name or "",
                        arguments=call.args or {},
                        provider_data=(
                            {"thought_signature": part.thought_signature}
                            if part.thought_signature
                            else {}
                        ),
                    )
                )
        metadata = response.usage_metadata
        return Response(
            text="".join(text),
            tool_calls=tool_calls,
            stop_reason=_stop_reason(response, tool_calls),
            usage=map_usage(metadata),
            latency_s=latency,
            raw={
                "usage": metadata.model_dump(mode="json", exclude_none=True) if metadata else {},
                "model_version": response.model_version,
                "finish_reason": str(candidate.finish_reason) if candidate else None,
            },
        )
