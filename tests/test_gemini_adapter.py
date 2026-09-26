import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from google import genai
from google.genai import types

from dowbench.agent.loop import Ceiling, run_episode
from dowbench.agent.tools import ToolBox
from dowbench.metering.usage import Usage
from dowbench.providers.base import Message, Request, UsageMappingError
from dowbench.providers.gemini_api import USAGE_FIELDS, GeminiProvider, usage_from_gemini

FIXTURES = Path(__file__).parent / "fixtures" / "gemini"


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


class Recorder:
    """In-process transport: serves fixture bodies in order and keeps request bodies."""

    def __init__(self, *responses: dict[str, Any]) -> None:
        self._responses = list(responses)
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=self._responses.pop(0))

    def provider(self) -> GeminiProvider:
        client = genai.Client(
            api_key="fixture-placeholder",
            http_options=types.HttpOptions(
                httpx_client=httpx.Client(transport=httpx.MockTransport(self.handler))
            ),
        )
        return GeminiProvider(client)


def _request(*messages: Message) -> Request:
    return Request(
        model="gemini-2.5-flash",
        system="You are a helpful agent.",
        messages=list(messages) or [Message(role="user", content="Summarize the Q3 report.")],
        tools=ToolBox("fetch_doc").specs,
        max_tokens=256,
    )


def test_usage_fields_match_installed_sdk() -> None:
    assert set(types.GenerateContentResponseUsageMetadata.model_fields) == USAGE_FIELDS


def test_function_call_response_is_normalized() -> None:
    recorder = Recorder(_fixture("function_call.json"))
    response = recorder.provider().complete(_request())

    assert response.stop_reason == "tool_use"
    assert response.text == ""  # thought parts are not answer text
    [call] = response.tool_calls
    assert call.name == "fetch_doc"
    assert call.arguments == {"doc_id": "q3-report", "page": 1}
    assert call.id.startswith("dowbench-")
    # prompt 500 includes 100 cached; thoughts are separate from candidates.
    assert response.usage == Usage(
        input_tokens=400, cache_read_tokens=100, output_tokens=20, reasoning_tokens=80
    )
    assert response.usage.total_tokens == 600


def test_request_serialization() -> None:
    recorder = Recorder(_fixture("final_answer.json"))
    recorder.provider().complete(_request())
    path, body = recorder.requests[0]
    assert path.endswith("/models/gemini-2.5-flash:generateContent")
    assert body["systemInstruction"]["parts"] == [{"text": "You are a helpful agent."}]
    assert body["generationConfig"]["maxOutputTokens"] == 256
    declarations = body["tools"][0]["functionDeclarations"]
    assert [d["name"] for d in declarations] == ["fetch_doc", "search"]
    assert declarations[0]["parameters_json_schema"]["required"] == ["doc_id"]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "Summarize the Q3 report."}]}]


def test_usage_that_does_not_add_up_is_rejected() -> None:
    meta = copy.deepcopy(_fixture("function_call.json")["usageMetadata"])
    meta["totalTokenCount"] = 601
    with pytest.raises(UsageMappingError, match="does not add up"):
        usage_from_gemini(types.GenerateContentResponseUsageMetadata.model_validate(meta))


def test_missing_usage_is_rejected() -> None:
    with pytest.raises(UsageMappingError, match="no usage_metadata"):
        usage_from_gemini(None)


@pytest.mark.parametrize(
    ("finish", "expected"),
    [("MAX_TOKENS", "max_tokens"), ("SAFETY", "refusal"), ("MALFORMED_FUNCTION_CALL", "other")],
)
def test_finish_reasons(finish: str, expected: str) -> None:
    body = _fixture("final_answer.json")
    body["candidates"][0]["finishReason"] = finish
    response = Recorder(body).provider().complete(_request())
    assert response.stop_reason == expected


def test_blocked_prompt_is_a_refusal_with_billed_input() -> None:
    body = {"usageMetadata": {"promptTokenCount": 50, "totalTokenCount": 50}}
    response = Recorder(body).provider().complete(_request())
    assert response.stop_reason == "refusal"
    assert response.usage == Usage(input_tokens=50)


def test_count_tokens_is_not_used() -> None:
    recorder = Recorder()
    assert recorder.provider().count_tokens(_request()) is None
    assert recorder.requests == []


def test_agent_loop_replays_thought_signature_and_returns_function_response() -> None:
    recorder = Recorder(_fixture("function_call.json"), _fixture("final_answer.json"))
    result = run_episode(
        recorder.provider(),
        model="gemini-2.5-flash",
        system="You are a helpful agent.",
        user_prompt="Summarize the Q3 report.",
        toolbox=ToolBox("fetch_doc"),
        defenses=[],
        ceiling=Ceiling(),
    )
    assert result.status == "completed"
    assert result.usage.total_tokens == 600 + 712

    _, second = recorder.requests[1]
    model_turn, function_turn = second["contents"][1:]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][1]["thoughtSignature"] == "Zml4dHVyZS1zaWc="
    assert function_turn["role"] == "user"
    response = function_turn["parts"][0]["functionResponse"]
    assert response["name"] == "fetch_doc"
    assert "id" not in response  # synthetic ids are never sent back
    assert "Quarterly report" in response["response"]["output"]
