"""Gemini adapter tests against a fake client: no network, no quota."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from google.genai import types

from dowbench.attacks.schema import load_dataset
from dowbench.metering.usage import Usage
from dowbench.providers.base import (
    Message,
    ProviderSetupError,
    Request,
    ToolCall,
    ToolSpec,
    UsageMappingError,
)
from dowbench.providers.gemini import (
    API_URL,
    REQUEST_TIMEOUT_S,
    TEMPLATE_MARGIN_TOKENS,
    GeminiProvider,
    map_usage,
    to_contents,
)
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import build_provider

PILOT = Path(__file__).parent.parent / "configs" / "pilot.yaml"

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


def test_model_turn_is_replayed_verbatim_with_thought_signatures() -> None:
    parts = [
        types.Part(text="thinking", thought=True, thought_signature=b"sig-text"),
        types.Part(
            function_call=types.FunctionCall(id="c1", name="search", args={"q": "a"}),
            thought_signature=b"sig",
        ),
    ]
    response = GeminiProvider(FakeClient(FakeModels(_response(parts)))).complete(_request())
    assert "provider_data" not in response.model_dump()

    contents = to_contents(
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                tool_calls=response.tool_calls,
                provider_data=response.provider_data,
            ),
            Message(role="tool", content="r", tool_call_id="c1"),
        ]
    )
    replayed = contents[1].parts or []
    assert [p.thought_signature for p in replayed] == [b"sig-text", b"sig"]
    assert replayed[0].thought is True


# --- client setup (ADR 0005) ---


def test_missing_key_refuses_to_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "other-account")
    monkeypatch.setenv("GEMINI_API_KEY", "  ")
    with pytest.raises(ProviderSetupError, match="GEMINI_API_KEY"):
        GeminiProvider()


def test_client_gets_explicit_key_developer_api_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[dict[str, Any]] = []

    class RecordingClient:
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

    monkeypatch.setattr("google.genai.Client", RecordingClient)
    monkeypatch.setenv("GOOGLE_API_KEY", "other-account")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    GeminiProvider()
    options: types.HttpOptions = built[0]["http_options"]
    assert built[0]["api_key"] == "gemini-key"
    assert built[0]["vertexai"] is False
    assert options.timeout == REQUEST_TIMEOUT_S * 1000
    assert options.retry_options is None


def test_build_provider_without_sdk_explains_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "dowbench.providers.gemini", raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", None)
    config = RunConfig.model_validate(
        {**RunConfig.from_yaml(PILOT).model_dump(mode="json"), "provider": "gemini"}
    )
    with pytest.raises(ProviderSetupError, match=r"dowbench\[gemini\]"):
        build_provider(config, load_dataset())


def test_real_client_ignores_base_url_and_replay_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.genai._api_client import BaseApiClient

    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "http://127.0.0.1:9/attacker/")
    monkeypatch.setenv("GOOGLE_GENAI_CLIENT_MODE", "replay")
    monkeypatch.setenv("GOOGLE_GENAI_REPLAYS_DIRECTORY", "/tmp/replays")
    monkeypatch.setenv("GOOGLE_GENAI_REPLAY_ID", "canned")
    provider: Any = GeminiProvider()
    api = provider._client._api_client
    assert type(api) is BaseApiClient  # not the ReplayApiClient subclass
    assert api._http_options.base_url == API_URL
    assert api.api_key == "gemini-key"
    assert api.vertexai is False


def test_request_body_is_what_was_sent_including_the_replayed_signature() -> None:
    parts = [
        types.Part(
            function_call=types.FunctionCall(id="c1", name="search", args={"q": "a"}),
            thought_signature=b"sig",
        )
    ]
    first = GeminiProvider(FakeClient(FakeModels(_response(parts)))).complete(_request())
    assert first.model_version == "gemini-test-001"

    models = FakeModels(_response([types.Part(text="done")]))
    follow_up = _request(
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant", tool_calls=first.tool_calls, provider_data=first.provider_data
            ),
            Message(role="tool", content="r", tool_call_id="c1"),
        ]
    )
    body = GeminiProvider(FakeClient(models)).complete(follow_up).request_body
    sent = models.generate_calls[0]
    assert body["contents"] == [
        c.model_dump(mode="json", exclude_none=True) for c in sent["contents"]
    ]
    replayed = body["contents"][1]["parts"][0]
    assert replayed["function_call"]["name"] == "search"
    assert replayed["thought_signature"] == "c2ln"  # base64 of b"sig"
    assert body["config"]["system_instruction"] == "Be brief."
    assert "api_key" not in json.dumps(body) and "http_options" not in body["config"]
