import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Usage as SdkUsage

from dowbench.agent.loop import Ceiling, run_episode
from dowbench.agent.tools import ToolBox
from dowbench.metering.usage import Usage
from dowbench.providers.anthropic_api import (
    USAGE_FIELDS,
    AnthropicProvider,
    usage_from_anthropic,
)
from dowbench.providers.base import Message, Request, UsageMappingError

FIXTURES = Path(__file__).parent / "fixtures" / "anthropic"


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


class Recorder:
    """In-process transport: serves fixture bodies in order and keeps request bodies."""

    def __init__(self, *messages: dict[str, Any]) -> None:
        self._messages = list(messages)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        if request.url.path.endswith("/count_tokens"):
            return httpx2.Response(200, json=_fixture("count_tokens.json"))
        return httpx2.Response(200, json=self._messages.pop(0))

    def provider(self) -> AnthropicProvider:
        client = anthropic.Anthropic(
            api_key="fixture-placeholder",
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self.handler)),
        )
        return AnthropicProvider(client)


def _request(*messages: Message) -> Request:
    return Request(
        model="claude-haiku-4-5",
        system="You are a helpful agent.",
        messages=list(messages) or [Message(role="user", content="Summarize the Q3 report.")],
        tools=ToolBox("fetch_doc").specs,
        max_tokens=256,
    )


def test_usage_fields_match_installed_sdk() -> None:
    assert set(SdkUsage.model_fields) == USAGE_FIELDS


def test_tool_use_response_is_normalized() -> None:
    recorder = Recorder(_fixture("tool_use.json"))
    response = recorder.provider().complete(_request())

    assert response.stop_reason == "tool_use"
    assert response.text == "Fetching the report."
    [call] = response.tool_calls
    assert (call.id, call.name, call.arguments) == (
        "toolu_fixture_1",
        "fetch_doc",
        {"doc_id": "q3-report", "page": 1},
    )
    assert response.usage == Usage(input_tokens=812, output_tokens=64)
    assert response.native is not None and response.native.provider == "anthropic"
    assert response.raw["id"] == "msg_fixture_tool_use"


def test_request_serialization() -> None:
    recorder = Recorder(_fixture("tool_use.json"))
    recorder.provider().complete(_request())
    path, body = recorder.requests[0]
    assert path == "/v1/messages"
    assert body["model"] == "claude-haiku-4-5"
    assert body["max_tokens"] == 256
    assert body["system"] == "You are a helpful agent."
    assert [t["name"] for t in body["tools"]] == ["fetch_doc", "search"]
    assert body["tools"][0]["input_schema"]["required"] == ["doc_id"]
    assert body["messages"] == [{"role": "user", "content": "Summarize the Q3 report."}]


def test_thinking_is_split_out_of_output_and_cache_is_mapped() -> None:
    recorder = Recorder(_fixture("thinking_end_turn.json"))
    response = recorder.provider().complete(_request())
    assert response.stop_reason == "end_turn"
    assert response.text == "Revenue grew 4%."
    assert response.usage == Usage(
        input_tokens=900,
        output_tokens=80,
        reasoning_tokens=220,
        cache_read_tokens=1000,
        cache_write_tokens=200,
    )
    # The billed output total is preserved exactly.
    assert response.usage.output_tokens + response.usage.reasoning_tokens == 300


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda u: u.update(new_billing_dimension=5), "unmapped"),
        (
            lambda u: u.update(server_tool_use={"web_search_requests": 1, "web_fetch_requests": 0}),
            "server tool",
        ),
        (
            lambda u: u.update(
                cache_creation={"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 9}
            ),
            "1-hour",
        ),
    ],
)
def test_unpriced_usage_is_rejected(mutate: Callable[[dict[str, Any]], None], message: str) -> None:
    raw = copy.deepcopy(_fixture("tool_use.json")["usage"])
    mutate(raw)
    with pytest.raises(UsageMappingError, match=message):
        usage_from_anthropic(SdkUsage.model_validate(raw))


def test_count_tokens_uses_free_endpoint_without_max_tokens() -> None:
    recorder = Recorder()
    assert recorder.provider().count_tokens(_request()) == 812
    path, body = recorder.requests[0]
    assert path == "/v1/messages/count_tokens"
    assert "max_tokens" not in body


def test_agent_loop_replays_native_blocks_and_groups_tool_results() -> None:
    thinking_tool_use = _fixture("tool_use.json")
    thinking_tool_use["content"].insert(
        0, {"type": "thinking", "thinking": "", "signature": "sig-1"}
    )
    recorder = Recorder(thinking_tool_use, _fixture("thinking_end_turn.json"))
    result = run_episode(
        recorder.provider(),
        model="claude-haiku-4-5",
        system="You are a helpful agent.",
        user_prompt="Summarize the Q3 report.",
        toolbox=ToolBox("fetch_doc"),
        defenses=[],
        ceiling=Ceiling(),
    )
    assert result.status == "completed"
    assert result.turns == 2

    create_calls = [body for path, body in recorder.requests if path == "/v1/messages"]
    assistant, tool_results = create_calls[1]["messages"][1:]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig-1"}
    assert tool_results["role"] == "user"
    assert tool_results["content"][0]["type"] == "tool_result"
    assert tool_results["content"][0]["tool_use_id"] == "toolu_fixture_1"
