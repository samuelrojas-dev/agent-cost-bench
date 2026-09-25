"""Defense middleware: hooks around each model call and each tool result."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from dowbench.agent.state import EpisodeState
from dowbench.providers.base import Request, Response, ToolCall


@dataclass(frozen=True)
class Abort:
    reason: str


class Defense:
    """Stateless between episodes: per-episode data lives in ``EpisodeState``.

    Hooks run in this order each turn: ``before_call`` (may rewrite the request),
    ``after_call`` (state already includes the response's usage, not its tool calls),
    then ``on_tool_result`` for each tool result (may rewrite it).
    """

    name: ClassVar[str]

    def before_call(self, request: Request, state: EpisodeState) -> Request | Abort:
        return request

    def after_call(self, response: Response, state: EpisodeState) -> Abort | None:
        return None

    def on_tool_result(self, call: ToolCall, result: str, state: EpisodeState) -> str | Abort:
        return result
