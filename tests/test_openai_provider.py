"""OpenAI adapter tests against a fake client: no network, no quota (ADR 0023)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from openai.types.chat import ChatCompletion

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
from dowbench.providers.openai import (
    API_URL,
    OpenAIProvider,
    map_sut_response,
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


def _completion(
    *,
    content: str | None = "ok",
    tool_calls: list[dict[str, Any]] | None = None,
    finish: str = "stop",
    usage: dict[str, Any] | None = None,
) -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-6-luna",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                }
            ],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    )


class FakeCompletions:
    def __init__(self, completion: ChatCompletion) -> None:
        self.completion = completion
        self.created: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> ChatCompletion:
        self.created.append(kwargs)
        return self.completion


class FakeChat:
    def __init__(self, completions: FakeCompletions) -> None:
        self.completions = completions


class FakeClient:
    def __init__(self, chat: FakeChat) -> None:
        self.chat = chat


def _provider(completion: ChatCompletion) -> tuple[OpenAIProvider, FakeCompletions]:
    fake = FakeCompletions(completion)
    return OpenAIProvider(FakeClient(FakeChat(fake))), fake


def _request(messages: list[Message] | None = None, **kwargs: Any) -> Request:
    return Request(
        model="gpt-6-luna",
        system=kwargs.pop("system", "Be brief."),
        messages=messages or [Message(role="user", content="hi")],
        tools=kwargs.pop("tools", [SEARCH]),
        max_tokens=kwargs.pop("max_tokens", 256),
    )


def _tc(id_: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": id_,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


# --- usage mapping (ADR 0002, ADR 0023) ---


def test_usage_maps_cached_and_reasoning_disjointly() -> None:
    usage = map_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 400,
            "total_tokens": 500,
            "prompt_tokens_details": {"cached_tokens": 60},
            "completion_tokens_details": {"reasoning_tokens": 300},
        }
    )
    assert usage == Usage(
        input_tokens=40, cache_read_tokens=60, output_tokens=100, reasoning_tokens=300
    )
    # the sum is the billed total; the split must not change it
    assert usage.total_tokens == 500


def test_usage_without_details() -> None:
    assert map_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}) == Usage(
        input_tokens=10, output_tokens=5
    )


@pytest.mark.parametrize(
    ("usage", "match"),
    [
        ({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 99}, "total_tokens"),
        ({"prompt_tokens": 10, "completion_tokens": 5, "system_tokens": 3}, "unknown OpenAI usage"),
        (
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_tokens_details": {"audio_tokens": 2},
            },
            "audio input",
        ),
        (
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "completion_tokens_details": {"audio_tokens": 2},
            },
            "audio output",
        ),
        (
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "completion_tokens_details": {"accepted_prediction_tokens": 2},
            },
            "predicted-output",
        ),
        (
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_tokens_details": {"brand_new": 1},
            },
            "prompt_tokens_details",
        ),
        (
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "completion_tokens_details": {"reasoning_tokens": 9},
            },
            "exceeds completion",
        ),
        (
            {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 99},
            },
            "exceeds prompt",
        ),
    ],
)
def test_usage_refuses_what_cannot_be_priced(usage: dict[str, Any], match: str) -> None:
    with pytest.raises(UsageMappingError, match=match):
        map_usage(usage)


# --- request mapping ---


def test_messages_prepend_system_and_round_trip_tool_calls() -> None:
    out = to_messages(
        "Be brief.",
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                content="looking",
                tool_calls=[ToolCall(id="a", name="search", arguments={"q": "x"})],
            ),
            Message(role="tool", content="r1", tool_call_id="a"),
        ],
    )
    assert out[0] == {"role": "system", "content": "Be brief."}
    assert out[1] == {"role": "user", "content": "hi"}
    assert out[2]["role"] == "assistant" and out[2]["content"] == "looking"
    assert out[2]["tool_calls"] == [_tc("a", "search", {"q": "x"})]
    assert out[3] == {"role": "tool", "tool_call_id": "a", "content": "r1"}


def test_empty_system_is_omitted_and_tools_shape() -> None:
    _, fake = _provider(_completion())
    provider = OpenAIProvider(FakeClient(FakeChat(fake)))
    provider.complete(_request(system=""))
    sent = fake.created[0]
    assert sent["messages"][0]["role"] == "user"  # no system message
    assert sent["max_completion_tokens"] == 256
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": SEARCH.description,
                "parameters": SEARCH.parameters,
            },
        }
    ]


# --- response mapping ---


def test_complete_maps_text_tool_calls_and_usage() -> None:
    completion = _completion(
        content="Searching.",
        tool_calls=[_tc("tc_1", "search", {"q": "a"})],
        finish="tool_calls",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 50,
            "total_tokens": 60,
            "completion_tokens_details": {"reasoning_tokens": 30},
        },
    )
    provider, _ = _provider(completion)
    response = provider.complete(_request())
    assert response.text == "Searching."
    assert response.stop_reason == "tool_use"
    assert [(c.id, c.name, c.arguments) for c in response.tool_calls] == [
        ("tc_1", "search", {"q": "a"})
    ]
    assert response.usage == Usage(input_tokens=10, output_tokens=20, reasoning_tokens=30)
    assert response.model_version == "gpt-6-luna"
    json.dumps(response.request_body)  # plain JSON, ready for requests.jsonl


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        ("stop", "end_turn"),
        ("tool_calls", "tool_use"),
        ("function_call", "tool_use"),
        ("length", "max_tokens"),
        ("content_filter", "refusal"),
    ],
)
def test_finish_reasons(finish: str, expected: str) -> None:
    provider, _ = _provider(_completion(content="x", finish=finish))
    assert provider.complete(_request()).stop_reason == expected


def test_unmapped_finish_reason_falls_back_to_other() -> None:
    from dowbench.providers.openai import _STOP_REASONS

    assert _STOP_REASONS.get("brand_new", "other") == "other"


def test_assistant_turn_is_replayed_verbatim() -> None:
    completion = _completion(
        content=None, tool_calls=[_tc("tc_1", "search", {"q": "a"})], finish="tool_calls"
    )
    provider, _ = _provider(completion)
    response = provider.complete(_request())
    assert "provider_data" not in response.model_dump()

    out = to_messages(
        "sys",
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                tool_calls=response.tool_calls,
                provider_data=response.provider_data,
            ),
            Message(role="tool", content="r", tool_call_id="tc_1"),
        ],
    )
    # The stored assistant message (with its tool_calls) is replayed unchanged.
    assert out[2]["role"] == "assistant"
    assert out[2]["tool_calls"][0]["id"] == "tc_1"


def test_count_tokens_is_none_and_provider_does_not_count() -> None:
    provider, _ = _provider(_completion())
    assert provider.count_tokens(_request()) is None
    assert provider.counts_tokens is False


# --- client setup (ADR 0005) ---


def test_missing_key_refuses_to_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", " ")
    with pytest.raises(ProviderSetupError, match="OPENAI_API_KEY"):
        OpenAIProvider()


def test_base_url_refuses_to_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.example")
    with pytest.raises(ProviderSetupError, match="OPENAI_BASE_URL"):
        OpenAIProvider()


def test_client_gets_explicit_key_official_url_and_no_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    client: Any = OpenAIProvider()._client
    assert client.api_key == "openai-key"
    assert str(client.base_url).rstrip("/") == API_URL
    assert client.max_retries == 0


def test_build_provider_without_sdk_explains_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    config = RunConfig.model_validate(
        {**RunConfig.from_yaml(PILOT).model_dump(mode="json"), "provider": "openai"}
    )
    with pytest.raises(ProviderSetupError, match=r"dowbench\[openai\]"):
        build_provider(config, load_dataset())


# --- agent-run metering (ADR 0012) ---


def test_map_sut_response_meters_a_completion() -> None:
    completion = _completion(usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
    mapped = map_sut_response(completion)
    assert mapped is not None
    usage, raw, model = mapped
    assert usage == Usage(input_tokens=7, output_tokens=3)
    assert model == "gpt-6-luna" and raw["prompt_tokens"] == 7


def test_map_sut_response_ignores_other_objects() -> None:
    assert map_sut_response({"not": "a completion"}) is None


# --- estimate / budget accounting (ADR 0023) ---


def test_estimate_uses_the_post_call_two_times_worst_case() -> None:
    from dowbench.metering.pricing import PriceTable
    from dowbench.runner.execute import estimate
    from dowbench.runner.matrix import plan_episodes

    config = RunConfig.from_yaml(Path(__file__).parent.parent / "configs" / "pilot-openai.yaml")
    dataset = load_dataset()
    specs = plan_episodes(config, dataset)
    result = estimate(config, specs, PriceTable.load(), set())
    # OpenAI cannot pre-count, so the worst case allows one overshoot: 2x the ceiling per episode.
    assert result.counted_before_calls is False
    assert result.worst_case_tokens == config.ceiling.max_total_tokens * 2 * result.pending
