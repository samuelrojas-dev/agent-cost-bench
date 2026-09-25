"""Budget-style defenses: cap tokens, turns and repeated tool calls."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from dowbench.agent.state import EpisodeState
from dowbench.defenses.base import Abort, Defense
from dowbench.providers.base import Request, Response


def _require_positive(**values: int) -> None:
    for name, value in values.items():
        if value < 1:
            raise ValueError(f"{name} must be >= 1, got {value}")


@dataclass(frozen=True)
class TokenBudget(Defense):
    """Caps billed tokens per task and max_tokens per call."""

    name = "token_budget"
    max_total_tokens: int = 8000
    max_tokens_per_call: int = 512

    def __post_init__(self) -> None:
        _require_positive(
            max_total_tokens=self.max_total_tokens, max_tokens_per_call=self.max_tokens_per_call
        )

    def before_call(self, request: Request, state: EpisodeState) -> Request | Abort:
        remaining = self.max_total_tokens - state.usage.total_tokens
        if remaining <= 0:
            return Abort(f"token budget of {self.max_total_tokens} exhausted")
        cap = min(request.max_tokens, self.max_tokens_per_call, remaining)
        return request.model_copy(update={"max_tokens": cap})


@dataclass(frozen=True)
class TurnLimit(Defense):
    """Caps the number of model calls per task."""

    name = "turn_limit"
    max_turns: int = 6

    def __post_init__(self) -> None:
        _require_positive(max_turns=self.max_turns)

    def before_call(self, request: Request, state: EpisodeState) -> Request | Abort:
        if state.turns >= self.max_turns:
            return Abort(f"turn limit of {self.max_turns} reached")
        return request


@dataclass(frozen=True)
class LoopDetect(Defense):
    """Stops the agent when it repeats a tool call or overuses one tool."""

    name = "loop_detect"
    max_identical_calls: int = 2
    max_calls_per_tool: int = 4

    def __post_init__(self) -> None:
        _require_positive(
            max_identical_calls=self.max_identical_calls,
            max_calls_per_tool=self.max_calls_per_tool,
        )

    def after_call(self, response: Response, state: EpisodeState) -> Abort | None:
        calls = [*state.tool_calls, *response.tool_calls]
        identical = Counter(call.signature() for call in calls)
        per_tool = Counter(call.name for call in calls)
        for call in response.tool_calls:
            if identical[call.signature()] > self.max_identical_calls:
                return Abort(f"identical call repeated: {call.signature()}")
            if per_tool[call.name] > self.max_calls_per_tool:
                return Abort(f"tool {call.name} called more than {self.max_calls_per_tool} times")
        return None
