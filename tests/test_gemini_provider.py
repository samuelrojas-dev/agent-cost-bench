"""Gemini adapter tests against a fake client: no network, no quota."""

from __future__ import annotations

import json
from typing import Any

import pytest
from google.genai import types

from dowbench.metering.usage import Usage
from dowbench.providers.base import Message, Request, ToolCall, ToolSpec
from dowbench.providers.gemini import (
    TEMPLATE_MARGIN_TOKENS,
    GeminiProvider,
    UsageMappingError,
    map_usage,
    to_contents,
)

SEARCH = ToolSpec(
    name="search",
    description="Search the knowledge base",
    parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
)


def _usage(**fields: Any) -> types.GenerateContentResponseUsageMetadata:
    return types.GenerateContentResponseUsageMetadata(**fields)


class FakeModels:
    def __init__(self, response: types.GenerateContentResponse, counted: int = 0) -> None:
        self.response = response
        self.counted = counted
        self.generate_calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []

    def generate_content(
        self, *, model: str, contents: Any, config: Any
    ) -> types.GenerateContentResponse:
        self.generate_calls.append({"model": model, "contents": contents, "config": config})
        return self.response

    def count_tokens(self, *, model: str, contents: Any) -> types.CountTokensResponse:
        self.count_calls.append({"model": model, "contents": contents})
        return types.CountTokensResponse(total_tokens=self.counted)


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


def _response(
    parts: list[types.Part],
    finish: types.FinishReason = types.FinishReason.STOP,
    usage: types.GenerateContentResponseUsageMetadata | None = None,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish)
        ],
        usage_metadata=usage
        or _usage(prompt_token_count=10, candidates_token_count=5, total_token_count=15),
        model_version="gemini-test-001",
    )


def _request(messages: list[Message] | None = None, **kwargs: Any) -> Request:
    return Request(
        model="gemini-test",
        system="Be brief.",
        messages=messages or [Message(role="user", content="hi")],
        tools=kwargs.pop("tools", [SEARCH]),
        max_tokens=kwargs.pop("max_tokens", 256),
    )


# --- usage mapping (ADR 0002) ---


def test_usage_maps_every_field_disjointly() -> None:
    usage = map_usage(
        _usage(
            prompt_token_count=1000,
            cached_content_token_count=600,
            candidates_token_count=50,
            thoughts_token_count=300,
            tool_use_prompt_token_count=20,
            total_token_count=1370,
        )
    )
    assert usage == Usage(
        input_tokens=420, cache_read_tokens=600, output_tokens=50, reasoning_tokens=300
    )
    assert usage.total_tokens == 1370


def test_usage_without_optional_fields() -> None:
    usage = map_usage(_usage(prompt_token_count=7, candidates_token_count=3, total_token_count=10))
    assert usage == Usage(input_tokens=7, output_tokens=3)


def test_usage_rejects_total_mismatch() -> None:
    with pytest.raises(UsageMappingError, match="reports 99"):
        map_usage(_usage(prompt_token_count=7, candidates_token_count=3, total_token_count=99))


def test_usage_rejects_unknown_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "dowbench.providers.gemini.KNOWN_USAGE_FIELDS", frozenset({"prompt_token_count"})
    )
    with pytest.raises(UsageMappingError, match="candidates_token_count"):
        map_usage(_usage(prompt_token_count=7, candidates_token_count=3))


def test_usage_rejects_missing_metadata() -> None:
    with pytest.raises(UsageMappingError, match="no usage_metadata"):
        map_usage(None)


def test_usage_rejects_cache_larger_than_prompt() -> None:
    with pytest.raises(UsageMappingError, match="exceed"):
        map_usage(_usage(prompt_token_count=5, cached_content_token_count=9))


# --- request mapping ---


def test_contents_round_trip_tool_calls_and_group_parallel_results() -> None:
    calls = [
        ToolCall(id="a", name="search", arguments={"q": "x"}),
        ToolCall(id="b", name="fetch", arguments={"url": "u"}),
    ]
    contents = to_contents(
        [
            Message(role="user", content="hi"),
            Message(role="assistant", content="", tool_calls=calls),
            Message(role="tool", content="r1", tool_call_id="a"),
            Message(role="tool", content="r2", tool_call_id="b"),
        ]
    )
    assert [c.role for c in contents] == ["user", "model", "user"]
    model_parts = contents[1].parts or []
    assert [p.function_call.name for p in model_parts if p.function_call] == ["search", "fetch"]
    results = [p.function_response for p in contents[2].parts or []]
    assert [(r.id, r.name, r.response) for r in results if r] == [
        ("a", "search", {"result": "r1"}),
        ("b", "fetch", {"result": "r2"}),
    ]


def test_contents_reject_orphan_tool_result() -> None:
    with pytest.raises(ValueError, match="unknown call"):
        to_contents([Message(role="tool", content="r", tool_call_id="zz")])


def test_complete_sends_system_tools_and_limits() -> None:
    models = FakeModels(_response([types.Part(text="ok")]))
    GeminiProvider(FakeClient(models)).complete(_request(max_tokens=128))
    sent = models.generate_calls[0]
    config: types.GenerateContentConfig = sent["config"]
    assert sent["model"] == "gemini-test"
    assert config.system_instruction == "Be brief."
    assert config.max_output_tokens == 128
    assert config.automatic_function_calling is not None
    assert config.automatic_function_calling.disable is True
    declarations = (config.tools or [])[0].function_declarations or []  # type: ignore[union-attr]
    assert declarations[0].name == "search"
    assert declarations[0].parameters_json_schema == SEARCH.parameters


# --- response mapping ---


def test_complete_maps_text_and_skips_thoughts() -> None:
    usage = _usage(
        prompt_token_count=10,
        candidates_token_count=4,
        thoughts_token_count=30,
        total_token_count=44,
    )
    parts = [types.Part(text="thinking...", thought=True), types.Part(text="answer")]
    response = GeminiProvider(FakeClient(FakeModels(_response(parts, usage=usage)))).complete(
        _request()
    )
    assert response.text == "answer"
    assert response.stop_reason == "end_turn"
    assert response.usage == Usage(input_tokens=10, output_tokens=4, reasoning_tokens=30)
    assert response.raw["usage"]["thoughts_token_count"] == 30
    assert response.raw["model_version"] == "gemini-test-001"


def test_complete_maps_function_calls() -> None:
    parts = [
        types.Part(function_call=types.FunctionCall(id="c1", name="search", args={"q": "a"})),
        types.Part(function_call=types.FunctionCall(name="search", args={"q": "b"})),
    ]
    response = GeminiProvider(FakeClient(FakeModels(_response(parts)))).complete(_request())
    assert response.stop_reason == "tool_use"
    assert [(c.id, c.arguments) for c in response.tool_calls] == [
        ("c1", {"q": "a"}),
        ("call_1_1", {"q": "b"}),
    ]


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        (types.FinishReason.MAX_TOKENS, "max_tokens"),
        (types.FinishReason.SAFETY, "refusal"),
        (types.FinishReason.MALFORMED_FUNCTION_CALL, "other"),
    ],
)
def test_finish_reasons(finish: types.FinishReason, expected: str) -> None:
    response = GeminiProvider(FakeClient(FakeModels(_response([], finish=finish)))).complete(
        _request()
    )
    assert response.stop_reason == expected


def test_blocked_prompt_is_a_refusal() -> None:
    blocked = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        ),
        usage_metadata=_usage(prompt_token_count=8, total_token_count=8),
    )
    response = GeminiProvider(FakeClient(FakeModels(blocked))).complete(_request())
    assert response.stop_reason == "refusal"
    assert response.usage == Usage(input_tokens=8)


# --- token counting (ADR 0004) ---


def test_count_tokens_bounds_system_and_tools_from_above() -> None:
    models = FakeModels(_response([]), counted=40)
    request = _request()
    counted = GeminiProvider(FakeClient(models)).count_tokens(request)
    tools_bytes = len(json.dumps([SEARCH.model_dump()]).encode())
    assert counted == 40 + len(request.system) + tools_bytes + TEMPLATE_MARGIN_TOKENS
    assert models.count_calls[0]["model"] == "gemini-test"
    assert models.generate_calls == []


def test_count_tokens_without_tools() -> None:
    models = FakeModels(_response([]), counted=40)
    counted = GeminiProvider(FakeClient(models)).count_tokens(_request(tools=[]))
    assert counted == 40 + len("Be brief.") + TEMPLATE_MARGIN_TOKENS


def test_thought_signature_round_trips_without_changing_call_identity() -> None:
    parts = [
        types.Part(
            function_call=types.FunctionCall(id="c1", name="search", args={"q": "a"}),
            thought_signature=b"sig",
        )
    ]
    call = (
        GeminiProvider(FakeClient(FakeModels(_response(parts)))).complete(_request()).tool_calls[0]
    )
    assert call.provider_data == {"thought_signature": b"sig"}
    assert call.signature() == ToolCall(id="x", name="search", arguments={"q": "a"}).signature()
    assert "provider_data" not in call.model_dump()

    contents = to_contents(
        [
            Message(role="user", content="hi"),
            Message(role="assistant", tool_calls=[call]),
            Message(role="tool", content="r", tool_call_id="c1"),
        ]
    )
    assert (contents[1].parts or [])[0].thought_signature == b"sig"
