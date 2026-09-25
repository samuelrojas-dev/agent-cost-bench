from dowbench.agent.tools import ToolBox
from dowbench.providers.base import Message, Request, ToolCall
from dowbench.providers.mock import MockProvider, approx_tokens

MARKER = "ATTACK-MARKER"


def _request(*messages: Message, max_tokens: int = 500) -> Request:
    return Request(
        model="mock-1",
        system="You are a helpful agent.",
        messages=list(messages),
        tools=ToolBox("fetch_doc").specs,
        max_tokens=max_tokens,
    )


def _after_tool(result: str) -> tuple[Message, ...]:
    call = ToolCall(id="c1", name="fetch_doc", arguments={"doc_id": "doc_id-1", "page": 1})
    return (
        Message(role="user", content="Summarize the doc."),
        Message(role="assistant", tool_calls=[call]),
        Message(role="tool", content=result, tool_call_id="c1"),
    )


def test_benign_calls_tool_then_answers() -> None:
    mock = MockProvider()
    first = mock.complete(_request(Message(role="user", content="Summarize the doc.")))
    assert first.stop_reason == "tool_use"
    assert first.tool_calls[0].name == "fetch_doc"

    second = mock.complete(_request(*_after_tool("clean result")))
    assert second.stop_reason == "end_turn"
    assert second.usage.output_tokens == 60
    assert second.usage.reasoning_tokens == 0


def test_usage_input_matches_count_tokens() -> None:
    mock = MockProvider()
    request = _request(*_after_tool("clean result"))
    assert mock.complete(request).usage.input_tokens == mock.count_tokens(request)


def test_is_deterministic() -> None:
    mock = MockProvider([(MARKER, "repeated_tool_calls")])
    request = _request(*_after_tool(MARKER))
    assert mock.complete(request) == mock.complete(request)


def test_identical_loop_repeats_same_arguments() -> None:
    mock = MockProvider([(MARKER, "repeated_identical_calls")])
    response = mock.complete(_request(*_after_tool(MARKER)))
    assert response.tool_calls[0].arguments == {"doc_id": "doc_id-1", "page": 1}


def test_paging_loop_advances_page() -> None:
    mock = MockProvider([(MARKER, "repeated_tool_calls")])
    response = mock.complete(_request(*_after_tool(MARKER)))
    assert response.tool_calls[0].arguments["page"] == 2


def test_output_flood_fills_max_tokens() -> None:
    mock = MockProvider([(MARKER, "long_output")])
    response = mock.complete(_request(Message(role="user", content=MARKER), max_tokens=321))
    assert response.stop_reason == "max_tokens"
    assert response.usage.output_tokens == 321
    assert approx_tokens(response.text) == 321


def test_reasoning_bomb_spends_budget_on_reasoning() -> None:
    mock = MockProvider([(MARKER, "long_reasoning")])
    response = mock.complete(_request(*_after_tool(MARKER), max_tokens=400))
    assert response.usage.output_tokens + response.usage.reasoning_tokens == 400
    assert response.stop_reason == "end_turn"


def test_context_growth_fetches_extra_pages_then_answers() -> None:
    mock = MockProvider([(MARKER, "context_growth")], extra_fetches=1)
    messages = list(_after_tool(MARKER))
    assert mock.complete(_request(*messages)).stop_reason == "tool_use"
    messages += [
        Message(role="assistant", tool_calls=[ToolCall(id="c2", name="fetch_doc")]),
        Message(role="tool", content=MARKER, tool_call_id="c2"),
    ]
    assert mock.complete(_request(*messages)).stop_reason == "end_turn"
