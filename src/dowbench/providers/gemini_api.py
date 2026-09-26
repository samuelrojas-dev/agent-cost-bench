"""Gemini adapter over the official ``google-genai`` SDK (ADR 0002, ADR 0004)."""

from __future__ import annotations

import time
from collections.abc import Sequence

from google import genai
from google.genai import types

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

PROVIDER = "gemini"

# Every field of GenerateContentResponseUsageMetadata this adapter knows about. A test
# fails when the SDK adds one, so a new billing dimension cannot be silently ignored.
USAGE_FIELDS = frozenset(
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

# Function calls without an API-assigned id get one of these; it is never sent back.
SYNTHETIC_ID_PREFIX = "dowbench-"

_REFUSALS = frozenset(
    {
        types.FinishReason.SAFETY,
        types.FinishReason.RECITATION,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.SPII,
    }
)


def usage_from_gemini(meta: types.GenerateContentResponseUsageMetadata | None) -> Usage:
    if meta is None:
        raise UsageMappingError("Gemini response has no usage_metadata")
    prompt = meta.prompt_token_count or 0
    cached = meta.cached_content_token_count or 0
    # prompt_token_count includes cached tokens; tool-use prompt tokens are extra input.
    usage = Usage(
        input_tokens=prompt - cached + (meta.tool_use_prompt_token_count or 0),
        cache_read_tokens=cached,
        output_tokens=meta.candidates_token_count or 0,
        reasoning_tokens=meta.thoughts_token_count or 0,
    )
    if meta.total_token_count is not None and usage.total_tokens != meta.total_token_count:
        raise UsageMappingError(
            f"Gemini usage does not add up: mapped {usage.total_tokens}, "
            f"total_token_count {meta.total_token_count}"
        )
    return usage


def to_gemini_contents(messages: Sequence[Message]) -> list[types.Content]:
    out: list[types.Content] = []
    for message in messages:
        if message.role == "user":
            out.append(types.Content(role="user", parts=[types.Part(text=message.content)]))
        elif message.role == "assistant":
            if message.native is not None and message.native.provider == PROVIDER:
                parts = [types.Part.model_validate(p) for p in message.native.content]
            else:
                parts = [types.Part(text=message.content)] if message.content else []
                parts += [
                    types.Part(function_call=types.FunctionCall(name=c.name, args=c.arguments))
                    for c in message.tool_calls
                ]
            out.append(types.Content(role="model", parts=parts))
        else:
            call_id = message.tool_call_id
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=None
                    if call_id is None or call_id.startswith(SYNTHETIC_ID_PREFIX)
                    else call_id,
                    name=message.tool_name,
                    response={"output": message.content},
                )
            )
            # All results of one model turn go back in a single user turn.
            previous = out[-1] if out else None
            if (
                previous is not None
                and previous.role == "user"
                and previous.parts
                and previous.parts[0].function_response is not None
            ):
                previous.parts.append(part)
            else:
                out.append(types.Content(role="user", parts=[part]))
    return out


def response_from_gemini(response: types.GenerateContentResponse, *, latency_s: float) -> Response:
    meta = response.usage_metadata
    usage = usage_from_gemini(meta)
    raw = {"usage": meta.model_dump(mode="json", exclude_none=True) if meta else {}}
    if not response.candidates:
        # The prompt itself was blocked: nothing generated, input still billed.
        return Response(stop_reason="refusal", usage=usage, latency_s=latency_s, raw=raw)

    candidate = response.candidates[0]
    parts = candidate.content.parts if candidate.content and candidate.content.parts else []
    text = "".join(p.text for p in parts if p.text and not p.thought)
    calls = [
        ToolCall(
            id=p.function_call.id or f"{SYNTHETIC_ID_PREFIX}{index}",
            name=p.function_call.name or "",
            arguments=dict(p.function_call.args or {}),
        )
        for index, p in enumerate(parts)
        if p.function_call is not None
    ]

    finish = candidate.finish_reason
    stop: StopReason
    if finish == types.FinishReason.MAX_TOKENS:
        stop = "max_tokens"
    elif finish in _REFUSALS:
        stop = "refusal"
    elif calls:
        stop = "tool_use"
    elif finish in (None, types.FinishReason.STOP):
        stop = "end_turn"
    else:
        stop = "other"

    return Response(
        text=text,
        tool_calls=calls,
        stop_reason=stop,
        usage=usage,
        latency_s=latency_s,
        raw=raw,
        native=NativeContent(
            provider=PROVIDER,
            content=[p.model_dump(mode="json", exclude_none=True) for p in parts],
        ),
    )


class GeminiProvider:
    name = PROVIDER
    simulated = False

    def __init__(self, client: genai.Client | None = None) -> None:
        # Credentials come from the environment (GEMINI_API_KEY); never pass them in code.
        self._client = client if client is not None else genai.Client()

    def count_tokens(self, request: Request) -> int | None:
        # Not used: see ADR 0004. The ceiling is enforced after each call instead.
        return None

    def complete(self, request: Request) -> Response:
        tools = (
            [
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters_json_schema=t.parameters,
                        )
                        for t in request.tools
                    ]
                )
            ]
            if request.tools
            else None
        )
        config = types.GenerateContentConfig(
            system_instruction=request.system or None,
            max_output_tokens=request.max_tokens,
            tools=tools,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        start = time.perf_counter()
        response = self._client.models.generate_content(
            model=request.model, contents=to_gemini_contents(request.messages), config=config
        )
        return response_from_gemini(response, latency_s=time.perf_counter() - start)
