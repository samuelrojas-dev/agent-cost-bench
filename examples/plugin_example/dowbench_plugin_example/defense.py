"""A custom defense contributed by a plugin (ADR 0021)."""

from __future__ import annotations

from dataclasses import dataclass

from dowbench.agent.state import EpisodeState
from dowbench.defenses.base import Defense
from dowbench.providers.base import ToolCall


@dataclass(frozen=True)
class HeadCap(Defense):
    """Keep only the first ``max_chars`` characters of every tool result."""

    name = "head_cap"
    max_chars: int = 500

    def __post_init__(self) -> None:
        if self.max_chars < 1:
            raise ValueError(f"max_chars must be >= 1, got {self.max_chars}")

    def on_tool_result(self, call: ToolCall, result: str, state: EpisodeState) -> str:
        return result[: self.max_chars]
