"""Provider-neutral request/response types and the Provider protocol (ADR 0002)."""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from dowbench.metering.usage import Usage

Role = Literal["user", "assistant", "tool"]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    def signature(self) -> str:
        """Identity of the call ignoring its id: name plus canonical arguments."""
        return f"{self.name}:{json.dumps(self.arguments, sort_keys=True)}"


class UsageMappingError(ValueError):
    """Provider usage that cannot be mapped to ``Usage`` without guessing (ADR 0002)."""


class NativeContent(BaseModel):
    """An assistant turn exactly as the provider returned it, as JSON.

    Providers require some blocks to be sent back unchanged (Claude thinking blocks,
    Gemini thought signatures), so adapters replay this instead of rebuilding the turn.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    content: list[dict[str, Any]]


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    native: NativeContent | None = None


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    parameters: dict[str, Any]


class Request(BaseModel):
    model: str
    system: str
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    max_tokens: int = Field(gt=0)


class Response(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason
    usage: Usage
    latency_s: float = Field(default=0.0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)
    native: NativeContent | None = None


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def simulated(self) -> bool:
        """True when usage is synthetic; such results are never published."""
        ...

    def complete(self, request: Request) -> Response: ...

    def count_tokens(self, request: Request) -> int | None:
        """Input tokens the request would bill, or None if the provider cannot count."""
        ...
