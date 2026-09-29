"""Deterministic, network-free provider for tests and development.

Its usage is simulated and must never be published as a result (CLAUDE.md, ADR 0002).
It behaves like a maximally susceptible model: once an attack payload is in its context,
it does what the attack's ``expected_signal`` describes; otherwise it answers after one
tool call, like a well-behaved agent.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from dowbench.attacks.schema import Dataset, Signal
from dowbench.metering.usage import Usage
from dowbench.providers.base import Request, Response, StopReason, ToolCall, ToolSpec


def approx_tokens(text: str) -> int:
    """Four characters per token: crude, but deterministic."""
    return math.ceil(len(text) / 4)


def build_mock(config: object, dataset: Dataset) -> MockProvider:
    """``dowbench.providers`` factory: the mock, primed with the dataset triggers (ADR 0021)."""
    return MockProvider([(a.marker, a.expected_signal) for a in dataset.attacks])


class MockProvider:
    name = "mock"
    simulated = True

    def __init__(
        self,
        triggers: Sequence[tuple[str, Signal]] = (),
        *,
        answer_tokens: int = 60,
        extra_fetches: int = 3,
    ) -> None:
        self._triggers = tuple(triggers)
        self._answer_tokens = answer_tokens
        self._extra_fetches = extra_fetches

    def count_tokens(self, request: Request) -> int:
        parts = [request.system]
        for message in request.messages:
            parts.append(message.content)
            parts += [call.model_dump_json() for call in message.tool_calls]
        parts += [tool.model_dump_json() for tool in request.tools]
        return sum(approx_tokens(part) for part in parts)

    def complete(self, request: Request) -> Response:
        response = self._complete(request)
        return response.model_copy(
            update={
                "model_version": f"{request.model}-simulated",
                "request_body": {"model": request.model, "messages": len(request.messages)},
            }
        )

    def _complete(self, request: Request) -> Response:
        input_tokens = self.count_tokens(request)
        signal = self._active_signal(request)
        tool_results = sum(1 for m in request.messages if m.role == "tool")

        if signal == "long_output":
            return self._answer(input_tokens, request.max_tokens, stop="max_tokens")
        if request.tools and tool_results > 0 and signal == "alternating_tool_calls":
            # A fresh reference each hop, alternating tools: never an identical call.
            tool = request.tools[tool_results % len(request.tools)]
            return self._call_tool(
                request, input_tokens, 1, tool=tool, value=f"ref-{tool_results + 1}"
            )
        if request.tools and tool_results > 0 and signal == "growing_arguments":
            # Pastes the whole previous result into the next call's arguments.
            previous = next(m.content for m in reversed(request.messages) if m.role == "tool")
            return self._call_tool(request, input_tokens, 1, value=previous)
        if request.tools and (
            tool_results == 0
            or signal == "repeated_identical_calls"
            or signal == "repeated_tool_calls"
            or (signal == "context_growth" and tool_results <= self._extra_fetches)
        ):
            page = 1 if signal in (None, "repeated_identical_calls") else tool_results + 1
            return self._call_tool(request, input_tokens, page)

        answer = min(self._answer_tokens, request.max_tokens)
        reasoning = request.max_tokens - answer if signal == "long_reasoning" else 0
        return self._answer(input_tokens, answer, reasoning=reasoning)

    def _active_signal(self, request: Request) -> Signal | None:
        texts = [m.content for m in request.messages]
        texts += [tool.description for tool in request.tools]
        for marker, signal in self._triggers:
            if any(marker in text for text in texts):
                return signal
        return None

    def _call_tool(
        self,
        request: Request,
        input_tokens: int,
        page: int,
        *,
        tool: ToolSpec | None = None,
        value: str | None = None,
    ) -> Response:
        """Call ``tool`` (default: the first) with every required argument set to ``value``
        (default: ``<name>-1``)."""
        tool = tool or request.tools[0]
        properties = tool.parameters.get("properties", {})
        arguments: dict[str, object] = {
            name: f"{name}-1" if value is None else value
            for name in tool.parameters.get("required", [])
        }
        if "page" in properties:
            arguments["page"] = page
        call = ToolCall(id=f"call_{len(request.messages)}", name=tool.name, arguments=arguments)
        output_tokens = approx_tokens(call.model_dump_json())
        if value is not None and output_tokens > request.max_tokens:
            # A real model stops mid-call at max_tokens; the call never reaches the tool.
            return self._answer(input_tokens, request.max_tokens, stop="max_tokens")
        usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
        return Response(
            tool_calls=[call],
            stop_reason="tool_use",
            usage=usage,
            raw={"usage": usage.model_dump()},
        )

    def _answer(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        reasoning: int = 0,
        stop: StopReason = "end_turn",
    ) -> Response:
        usage = Usage(
            input_tokens=input_tokens, output_tokens=output_tokens, reasoning_tokens=reasoning
        )
        return Response(
            text=("answer " * output_tokens)[: output_tokens * 4],
            stop_reason=stop,
            usage=usage,
            raw={"usage": usage.model_dump()},
        )
