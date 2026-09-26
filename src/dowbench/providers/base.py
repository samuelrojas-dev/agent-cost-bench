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
    # Opaque data the same provider needs back on the next turn (e.g. Gemini thought
    # signatures). Not part of the call's identity and never serialized.
    provider_data: dict[str, Any] = Field(default_factory=dict, exclude=True)

    def signature(self) -> str:
        """Identity of the call ignoring its id: name plus canonical arguments."""
        return f"{self.name}:{json.dumps(self.arguments, sort_keys=True)}"


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


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
