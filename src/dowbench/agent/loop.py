"""Minimal provider-neutral tool-use loop with defenses and a safety ceiling (ADR 0003)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dowbench.agent.state import EpisodeState
from dowbench.agent.tools import ToolBox
from dowbench.defenses.base import Abort, Defense
from dowbench.metering.usage import Usage
from dowbench.providers.base import Message, Provider, Request, StopReason, UsageMappingError

EpisodeStatus = Literal[
    "completed",  # the model gave a final answer
    "truncated",  # the model hit max_tokens on its final call
    "aborted",  # a defense stopped the episode
    "censored",  # the safety ceiling stopped the episode; cost is a lower bound
    "errored",  # a billed call could not be priced; cost is a lower bound (ADR 0007)
]


class Ceiling(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_turns: int = Field(default=12, ge=1)
    max_tokens_per_call: int = Field(default=1024, ge=1)
    max_total_tokens: int = Field(default=50_000, ge=1)


class CallRecord(BaseModel):
    turn: int
    max_tokens: int
    stop_reason: StopReason
    usage: Usage
    tool_calls: int
    latency_s: float
    # The provider's usage object as returned, kept so costs can be re-derived and the
    # mapping of ADR 0002 audited without a new paid run.
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    model_version: str | None = None
    # Written to requests.jsonl, sanitized, not to calls.jsonl (ADR 0009).
    request_body: dict[str, Any] = Field(default_factory=dict, exclude=True)


class EpisodeResult(BaseModel):
    status: EpisodeStatus
    reason: str | None = None
    turns: int
    usage: Usage
    calls: list[CallRecord]
    final_text: str = ""


def run_episode(
    provider: Provider,
    *,
    model: str,
    system: str,
    user_prompt: str,
    toolbox: ToolBox,
    defenses: Sequence[Defense],
    ceiling: Ceiling,
    on_call: Callable[[CallRecord], None] | None = None,
) -> EpisodeResult:
    """Run one episode. ``on_call`` sees every billed call as soon as it returns, so a
    later failure cannot lose it (ADR 0007)."""
    messages = [Message(role="user", content=user_prompt)]
    state = EpisodeState()
    calls: list[CallRecord] = []

    def record(call: CallRecord) -> None:
        calls.append(call)
        if on_call is not None:
            on_call(call)

    def finish(status: EpisodeStatus, reason: str | None = None, text: str = "") -> EpisodeResult:
        return EpisodeResult(
            status=status,
            reason=reason,
            turns=state.turns,
            usage=state.usage,
            calls=calls,
            final_text=text,
        )

    while True:
        if state.turns >= ceiling.max_turns:
            return finish("censored", f"ceiling: {ceiling.max_turns} turns")

        request = Request(
            model=model,
            system=system,
            messages=list(messages),
            tools=toolbox.specs,
            max_tokens=ceiling.max_tokens_per_call,
        )
        for defense in defenses:
            rewritten = defense.before_call(request, state)
            if isinstance(rewritten, Abort):
                return finish("aborted", f"{defense.name}: {rewritten.reason}")
            if rewritten.model != model or rewritten.tools != toolbox.specs:
                raise ValueError(f"{defense.name} changed the model or tools; costs would be wrong")
            request = rewritten

        input_tokens = provider.count_tokens(request)
        if input_tokens is None and not provider.simulated and provider.counts_tokens:
            raise ValueError(f"{provider.name} cannot count tokens; the ceiling would not hold")
        # A provider that declares counts_tokens=False (e.g. OpenAI, ADR 0023) skips the pre-call
        # gate below; the post-call total check (further down) enforces the ceiling instead.
        if (
            input_tokens is not None
            and state.usage.total_tokens + input_tokens + request.max_tokens
            > ceiling.max_total_tokens
        ):
            return finish("censored", f"ceiling: {ceiling.max_total_tokens} tokens")

        mapped = Usage()  # what is known of a billed call that then fails validation
        try:
            response = provider.complete(request)
            raw_usage = response.raw.get("usage")
            if not provider.simulated and not raw_usage:
                mapped = response.usage
                raise UsageMappingError(f"{provider.name} returned no raw usage")
        except UsageMappingError as exc:
            # Billed but unpriceable: record what the provider reported and stop (ADR 0007).
            state.turns += 1
            state.usage += mapped
            record(
                CallRecord(
                    turn=state.turns,
                    max_tokens=request.max_tokens,
                    stop_reason="other",
                    usage=mapped,
                    tool_calls=0,
                    latency_s=0.0,
                    raw_usage=exc.raw_usage,
                    error=str(exc),
                )
            )
            return finish("errored", f"{provider.name}: {exc}")
        state.turns += 1
        state.usage += response.usage
        record(
            CallRecord(
                turn=state.turns,
                max_tokens=request.max_tokens,
                stop_reason=response.stop_reason,
                usage=response.usage,
                tool_calls=len(response.tool_calls),
                latency_s=response.latency_s,
                raw_usage=raw_usage or {},
                model_version=response.model_version,
                request_body=response.request_body,
            )
        )

        for defense in defenses:
            abort = defense.after_call(response, state)
            if abort is not None:
                return finish("aborted", f"{defense.name}: {abort.reason}")
        if state.usage.total_tokens > ceiling.max_total_tokens:
            return finish("censored", f"ceiling: {ceiling.max_total_tokens} tokens")
        if response.stop_reason == "max_tokens":
            return finish("truncated", text=response.text)
        if not response.tool_calls:
            return finish("completed", text=response.text)

        messages.append(
            Message(
                role="assistant",
                content=response.text,
                tool_calls=response.tool_calls,
                provider_data=response.provider_data,
            )
        )
        for call in response.tool_calls:
            state.tool_calls.append(call)
            result = toolbox.run(call)
            for defense in defenses:
                filtered = defense.on_tool_result(call, result, state)
                if isinstance(filtered, Abort):
                    return finish("aborted", f"{defense.name}: {filtered.reason}")
                result = filtered
            messages.append(Message(role="tool", content=result, tool_call_id=call.id))
