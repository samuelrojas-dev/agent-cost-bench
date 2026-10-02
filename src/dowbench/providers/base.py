"""Provider-neutral request/response types and the Provider protocol (ADR 0002)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from dowbench.metering.usage import Usage

Role = Literal["user", "assistant", "tool"]
StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


class ProviderSetupError(RuntimeError):
    """The provider cannot be built: missing optional dependency or credentials."""


class TransientProviderError(RuntimeError):
    """A provider call was rejected for a temporary reason and is worth retrying.

    Rate limits (429) and 5xx responses are *rejected*, not billed, so retrying one adds no
    spend (ADR 0010, ADR 0020). Adapters raise this after classifying an SDK error; the
    ``RetryingProvider`` decorator retries it, and an exhausted retry leaves the episode
    unfinished so ``--resume`` runs it again rather than skipping it (#37).
    """


# HTTP statuses worth retrying: request timeout, conflict, too-early, rate limit, and the
# 5xx family a provider returns when temporarily unavailable.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_TRANSIENT_NAME = re.compile(r"timeout|timedout|connection|connect|overloaded", re.IGNORECASE)


def is_transient_error(exc: BaseException) -> bool:
    """Whether ``exc`` from a provider SDK is a temporary failure worth retrying.

    SDK-agnostic: an HTTP status in the retryable set (read from ``status_code`` or ``code``,
    the two attributes the Gemini and Anthropic SDKs use), or a timeout/connection error
    identified by its exception class name. Anything else is treated as terminal.
    """
    if isinstance(exc, TransientProviderError):
        return True
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "code", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS:
        return True
    return bool(_TRANSIENT_NAME.search(type(exc).__name__))


@contextmanager
def transient_boundary() -> Iterator[None]:
    """Re-raise a transient SDK error inside as ``TransientProviderError``; leave the rest.

    Adapters wrap their SDK call in this so a 429/5xx/timeout becomes the retryable type
    (ADR 0020) while `UsageMappingError`, `ProviderSetupError` and real bugs pass through.
    """
    try:
        yield
    except TransientProviderError:
        raise
    except Exception as exc:
        if is_transient_error(exc):
            raise TransientProviderError(str(exc)) from exc
        raise


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

    @property
    def counts_tokens(self) -> bool:
        """True when ``count_tokens`` returns a real count, so the ceiling is enforced before
        each call. False for a provider with no token-count endpoint (e.g. OpenAI): the ceiling
        is enforced after each call instead, and the worst case allows for one overshoot
        (ADR 0023)."""
        ...

    def complete(self, request: Request) -> Response: ...

    def count_tokens(self, request: Request) -> int | None:
        """Input tokens the request would bill, or None if the provider cannot count."""
        ...
