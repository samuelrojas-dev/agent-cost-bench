"""Mutable per-episode state shared by the agent loop and the defenses."""

from __future__ import annotations

from dataclasses import dataclass, field

from dowbench.metering.usage import Usage
from dowbench.providers.base import ToolCall


@dataclass
class EpisodeState:
    turns: int = 0
    usage: Usage = field(default_factory=Usage)
    tool_calls: list[ToolCall] = field(default_factory=list)
