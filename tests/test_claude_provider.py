"""Anthropic adapter tests against a fake client: no network, no quota."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message as ApiMessage
from anthropic.types import MessageTokensCount
from anthropic.types import Usage as ApiUsage

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
from dowbench.providers.claude import (
    API_URL,
    AnthropicProvider,
    map_usage,
    to_messages,
)
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import build_provider

PILOT = Path(__file__).parent.parent / "configs" / "pilot.yaml"

SEARCH = ToolSpec(
    name="search",
    description="Search the knowledge base",
    parameters={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
)


def _usage(**fields: Any) -> ApiUsage:
    return ApiUsage.model_validate({"input_tokens": 10, "output_tokens": 5, **fields})


def _message(
    content: list[dict[str, Any]], stop: str = "end_turn", usage: ApiUsage | None = None
) -> ApiMessage:
    return ApiMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": (usage or _usage()).model_dump(),
        }
    )


class FakeMessages:
    def __init__(self, message: ApiMessage, counted: int = 0) -> None:
        self.message = message
        self.counted = counted
        self.created: list[dict[str, Any]] = []
        self.counts: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> ApiMessage:
        self.created.append(kwargs)
        return self.message

    def count_tokens(self, **kwargs: Any) -> MessageTokensCount:
        self.counts.append(kwargs)
        return MessageTokensCount(input_tokens=self.counted)


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages


def _provider(message: ApiMessage, counted: int = 0) -> tuple[AnthropicProvider, FakeMessages]:
    fake = FakeMessages(message, counted)
    return AnthropicProvider(FakeClient(fake)), fake


def _request(messages: list[Message] | None = None, **kwargs: Any) -> Request:
    return Request(
        model="claude-test",
        system=kwargs.pop("system", "Be brief."),
        messages=messages or [Message(role="user", content="hi")],
        tools=kwargs.pop("tools", [SEARCH]),
        max_tokens=kwargs.pop("max_tokens", 256),
    )


# --- usage mapping (ADR 0002, ADR 0006) ---


def test_usage_maps_every_field_disjointly() -> None:
    usage = map_usage(
        _usage(
            input_tokens=100,
            cache_read_input_tokens=600,
            cache_creation_input_tokens=50,
            cache_creation={"ephemeral_5m_input_tokens": 50, "ephemeral_1h_input_tokens": 0},
            output_tokens=400,
            output_tokens_details={"thinking_tokens": 300},
            service_tier="standard",
            inference_geo="global",
        )
    )
    assert usage == Usage(
        input_tokens=100,
        cache_read_tokens=600,
        cache_write_tokens=50,
        output_tokens=100,
        reasoning_tokens=300,
    )
    # output_tokens is the billed total: the split must not change it.
    assert usage.output_tokens + usage.reasoning_tokens == 400


def test_usage_without_optional_fields() -> None:
    assert map_usage(_usage()) == Usage(input_tokens=10, output_tokens=5)


@pytest.mark.parametrize(
    ("fields", "match"),
    [
        ({"service_tier": "priority"}, "service tier"),
        ({"inference_geo": "us"}, "inference_geo"),
        (
            {"server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}},
            "server tool",
        ),
        (
            {
                "cache_creation_input_tokens": 9,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 9},
            },
            "1-hour",
        ),
        (
            {
                "cache_creation_input_tokens": 9,
                "cache_creation": {"ephemeral_5m_input_tokens": 4, "ephemeral_1h_input_tokens": 0},
            },
            "differs",
        ),
        ({"output_tokens_details": {"thinking_tokens": 6}}, "exceeds"),
        ({"iterations": [{"type": "fallback_message"}]}, "unknown"),
    ],
)
def test_usage_refuses_what_cannot_be_priced(fields: dict[str, Any], match: str) -> None:
    with pytest.raises(UsageMappingError, match=match):
        map_usage(_usage(**fields))


def test_zero_server_tool_use_is_accepted() -> None:
    usage = _usage(server_tool_use={"web_search_requests": 0, "web_fetch_requests": 0})
    assert map_usage(usage) == Usage(input_tokens=10, output_tokens=5)


# --- request mapping ---


def test_messages_round_trip_tool_calls_and_group_parallel_results() -> None:
    calls = [
        ToolCall(id="a", name="search", arguments={"q": "x"}),
        ToolCall(id="b", name="fetch", arguments={"url": "u"}),
    ]
    params = to_messages(
        [
            Message(role="user", content="hi"),
            Message(role="assistant", content="let me look", tool_calls=calls),
            Message(role="tool", content="r1", tool_call_id="a"),
            Message(role="tool", content="r2", tool_call_id="b"),
        ]
    )
    assert [p["role"] for p in params] == ["user", "assistant", "user"]
    assert list(params[1]["content"]) == [
        {"type": "text", "text": "let me look"},
        {"type": "tool_use", "id": "a", "name": "search", "input": {"q": "x"}},
        {"type": "tool_use", "id": "b", "name": "fetch", "input": {"url": "u"}},
    ]
    assert list(params[2]["content"]) == [
        {"type": "tool_result", "tool_use_id": "a", "content": "r1"},
        {"type": "tool_result", "tool_use_id": "b", "content": "r2"},
    ]


def test_count_and_create_send_the_same_request() -> None:
    provider, fake = _provider(_message([{"type": "text", "text": "ok"}]), counted=42)
    request = _request(max_tokens=128)
    assert provider.count_tokens(request) == 42
    provider.complete(request)
    sent = fake.created[0]
    assert sent.pop("max_tokens") == 128
    assert sent == fake.counts[0]
    assert sent["system"] == "Be brief."
    assert sent["tools"] == [
        {"name": "search", "description": SEARCH.description, "input_schema": SEARCH.parameters}
    ]
    # No thinking, effort, fallbacks or betas: the model's API defaults are measured.
    assert set(sent) == {"model", "messages", "system", "tools"}


def test_empty_system_and_tools_are_omitted() -> None:
    provider, fake = _provider(_message([{"type": "text", "text": "ok"}]))
    provider.complete(_request(system="", tools=[]))
    assert set(fake.created[0]) == {"model", "messages", "max_tokens"}


# --- response mapping ---


def test_complete_maps_text_tool_calls_and_skips_thinking() -> None:
    content: list[dict[str, Any]] = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Searching."},
        {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "a"}},
    ]
    usage = _usage(output_tokens=50, output_tokens_details={"thinking_tokens": 30})
    provider, _ = _provider(_message(content, stop="tool_use", usage=usage))
    response = provider.complete(_request())
    assert response.text == "Searching."
    assert response.stop_reason == "tool_use"
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [
        ("tu_1", "search", {"q": "a"})
    ]
    assert response.usage == Usage(input_tokens=10, output_tokens=20, reasoning_tokens=30)
    assert response.raw["usage"]["output_tokens_details"] == {"thinking_tokens": 30}
    assert response.raw["id"] == "msg_1"


@pytest.mark.parametrize(
    ("stop", "expected"),
    [
        ("end_turn", "end_turn"),
        ("max_tokens", "max_tokens"),
        ("model_context_window_exceeded", "max_tokens"),
        ("refusal", "refusal"),
        ("pause_turn", "other"),
        ("stop_sequence", "other"),
    ],
)
def test_stop_reasons(stop: str, expected: str) -> None:
    provider, _ = _provider(_message([], stop=stop))
    assert provider.complete(_request()).stop_reason == expected


def test_assistant_turn_is_replayed_verbatim_with_thinking() -> None:
    content: list[dict[str, Any]] = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "a"}},
    ]
    provider, _ = _provider(_message(content, stop="tool_use"))
    response = provider.complete(_request())
    assert "provider_data" not in response.model_dump()

    params = to_messages(
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                tool_calls=response.tool_calls,
                provider_data=response.provider_data,
            ),
            Message(role="tool", content="r", tool_call_id="tu_1"),
        ]
    )
    replayed = list(params[1]["content"])
    assert [block.type for block in replayed] == ["thinking", "tool_use"]  # type: ignore[union-attr]
    assert replayed[0].signature == "sig"  # type: ignore[union-attr]


# --- client setup (ADR 0005, ADR 0006) ---


def test_missing_key_refuses_to_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "other-account")
    monkeypatch.setenv("ANTHROPIC_API_KEY", " ")
    with pytest.raises(ProviderSetupError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()


def test_custom_headers_refuse_to_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "anthropic-beta: something")
    with pytest.raises(ProviderSetupError, match="ANTHROPIC_CUSTOM_HEADERS"):
        AnthropicProvider()


def test_client_gets_explicit_key_official_url_and_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "other-account")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.example")
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    client: Any = AnthropicProvider()._client
    assert client.api_key == "anthropic-key"
    assert client.auth_token is None
    assert str(client.base_url).rstrip("/") == API_URL
    assert client.max_retries == 0


def test_build_provider_without_sdk_explains_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "dowbench.providers.claude", raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    config = RunConfig.model_validate(
        {**RunConfig.from_yaml(PILOT).model_dump(mode="json"), "provider": "anthropic"}
    )
    with pytest.raises(ProviderSetupError, match=r"dowbench\[anthropic\]"):
        build_provider(config, load_dataset())


def test_request_body_and_model_version_are_reported() -> None:
    content: list[dict[str, Any]] = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "a"}},
    ]
    first_provider, _ = _provider(_message(content, stop="tool_use"))
    first = first_provider.complete(_request())
    assert first.model_version == "claude-test"

    provider, fake = _provider(_message([{"type": "text", "text": "done"}]))
    follow_up = _request(
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant", tool_calls=first.tool_calls, provider_data=first.provider_data
            ),
            Message(role="tool", content="r", tool_call_id="tu_1"),
        ]
    )
    body = provider.complete(follow_up).request_body
    assert body["max_tokens"] == fake.created[0]["max_tokens"]
    assert body["messages"][1]["content"][0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "sig",
    }
    json.dumps(body)  # plain JSON, ready for requests.jsonl
