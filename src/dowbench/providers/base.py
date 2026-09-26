"""Provider-neutral request/response types and the Provider protocol (ADR 0002)."""

from __future__ import annotations

import json
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from dowbench.metering.usage import Usage

Role = Literal["user", "assistant", "tool"]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


class ProviderSetupError(RuntimeError):
    """The provider cannot be built: missing optional dependency or credentials."""


class UsageMappingError(ValueError):
    """A billed response whose usage cannot be mapped or priced (ADR 0002, ADR 0007).

    ``raw_usage`` keeps the provider's usage object so the spend is still recorded.
    """

    def __init__(self, message: str, raw_usage: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.raw_usage = raw_usage or {}


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    def signature(self) -> str:
        """Identity of the call ignoring its id: name plus canonical arguments."""
        return f"{self.name}:{json.dumps(self.arguments, sort_keys=True)}"


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    # The assistant turn exactly as the provider returned it (thinking blocks, signatures),
    # replayed verbatim by the same provider on the next turn (ADR 0006). Never serialized.
    provider_data: dict[str, Any] = Field(default_factory=dict, exclude=True)


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
    provider_data: dict[str, Any] = Field(default_factory=dict, exclude=True)
    # Model version the provider says served the call, which may differ from the ID asked.
    model_version: str | None = None
    # The JSON body the adapter handed to the SDK (no headers, so no key). Persisted, after
    # sanitizing, to requests.jsonl for auditing (ADR 0009). Never part of the dump.
    request_body: dict[str, Any] = Field(default_factory=dict, exclude=True)


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
