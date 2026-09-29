"""Bring your own agent (ADR 0012): the benchmark owns tasks, tools and metering.

An agent under test is any object with ``run(task: Task) -> str``. It uses its own model,
prompt, loop and defenses, calls ``task.tools`` (some of which carry an attack), and hands
every provider response to ``task.meter.record(response)``. The meter maps usage with the
adapters' strict mappers, writes each call to disk at once and ends the episode at the
safety ceiling.
"""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from dowbench.agent.loop import CallRecord, Ceiling, EpisodeResult
from dowbench.agent.tools import ToolBox
from dowbench.metering.usage import Usage
from dowbench.providers.base import Response, ToolCall, UsageMappingError
from dowbench.registry import load

SUT_MAPPER_GROUP = "dowbench.sut_mappers"
# A mapper turns an SDK response object into billed usage, or None if it does not recognize
# the type. Providers register one under ``dowbench.sut_mappers`` (ADR 0021).
SutMapper = Callable[[object], "tuple[Usage, dict[str, Any], str | None] | None"]


def map_mock_response(response: object) -> tuple[Usage, dict[str, Any], str | None] | None:
    """Map the mock provider's own ``Response`` for an agent-run meter (ADR 0021)."""
    if isinstance(response, Response):
        return response.usage, response.raw.get("usage", {}), response.model_version
    return None


class StopEpisode(BaseException):
    """Ends an agent's episode from inside its loop.

    A ``BaseException`` so that an agent's ``except Exception`` cannot swallow it and keep
    spending past the ceiling.
    """

    def __init__(self, status: Literal["censored", "errored"], reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Tool:
    """A benchmark tool. Call it with keyword arguments; it returns text."""

    name: str
    description: str
    parameters: dict[str, Any]
    _box: ToolBox = field(repr=False, compare=False)

    def __call__(self, **arguments: Any) -> str:
        return self._box.run(ToolCall(id="agent", name=self.name, arguments=arguments))


class Meter:
    """Records every provider response of an episode and enforces the safety ceiling."""

    def __init__(
        self,
        *,
        provider: str,
        simulated: bool,
        ceiling: Ceiling,
        on_call: Callable[[CallRecord], None] | None = None,
    ) -> None:
        self._provider = provider
        self._simulated = simulated
        self._ceiling = ceiling
        self._on_call = on_call
        self.usage = Usage()
        self.calls: list[CallRecord] = []

    @property
    def turns(self) -> int:
        return len(self.calls)

    def record(self, response: object) -> None:
        """Account for one model call. Raises ``StopEpisode`` when the episode must end."""
        try:
            usage, raw, version = self._map(response)
            if not self._simulated and not raw:
                raise UsageMappingError(f"{self._provider} response has no usage", raw)
        except UsageMappingError as exc:
            self._emit(Usage(), exc.raw_usage, None, error=str(exc))
            raise StopEpisode("errored", f"{self._provider}: {exc}") from None
        self._emit(usage, raw, version)
        limit = self._ceiling.max_total_tokens
        if usage.total_tokens > limit:
            # The worst-case estimate assumes no single call exceeds the ceiling (ADR 0012).
            raise StopEpisode(
                "errored",
                f"one call billed {usage.total_tokens:,} tokens, over the {limit:,} ceiling",
            )
        if self.usage.total_tokens > limit:
            raise StopEpisode("censored", f"ceiling: {limit} tokens")
        if self.turns > self._ceiling.max_turns:
            raise StopEpisode("censored", f"ceiling: {self._ceiling.max_turns} turns")

    def _emit(
        self, usage: Usage, raw: dict[str, Any], version: str | None, error: str | None = None
    ) -> None:
        call = CallRecord(
            turn=self.turns + 1,
            max_tokens=self._ceiling.max_tokens_per_call,
            stop_reason="other",  # the agent owns the loop; stop reasons are not observed
            usage=usage,
            tool_calls=0,
            latency_s=0.0,
            raw_usage=raw,
            error=error,
            model_version=version,
        )
        self.calls.append(call)
        self.usage += usage
        if self._on_call is not None:
            self._on_call(call)

    def _map(self, response: object) -> tuple[Usage, dict[str, Any], str | None]:
        # Each provider registers how to meter its SDK response (ADR 0021); the mapper does
        # its own isinstance check and returns None if the object is not its type.
        try:
            mapper: SutMapper = load(SUT_MAPPER_GROUP, self._provider)
        except LookupError:
            mapper = None  # type: ignore[assignment]
        if mapper is not None:
            mapped = mapper(response)
            if mapped is not None:
                return mapped
        raise UsageMappingError(
            f"cannot meter a {type(response).__name__} for provider {self._provider!r}; pass "
            "the SDK's own response object"
        )


@dataclass(frozen=True)
class Task:
    """What the benchmark hands the agent for one episode."""

    prompt: str
    system_prompt: str
    tools: tuple[Tool, ...]
    max_tokens_per_call: int
    meter: Meter

    def tool(self, name: str) -> Tool:
        return next(t for t in self.tools if t.name == name)


class Agent(Protocol):
    def run(self, task: Task) -> str: ...


def load_agent(path: str) -> Agent:
    """Import ``module:attr``; a class or factory is called with no arguments.

    The current directory is put on ``sys.path`` first, as uvicorn does, so an agent module
    in the project that runs ``dowbench`` can be imported by the console script.
    """
    module_name, _, attr = path.partition(":")
    if not module_name or not attr:
        raise ValueError(f"agent must look like 'module:attr', got {path!r}")
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    obj = getattr(importlib.import_module(module_name), attr)
    agent = obj() if isinstance(obj, type) or not hasattr(obj, "run") else obj
    if not callable(getattr(agent, "run", None)):
        raise TypeError(f"{path} does not provide a run(task) method")
    return agent  # type: ignore[no-any-return]


def run_agent_episode(
    agent: Agent,
    *,
    provider: str,
    simulated: bool,
    system: str,
    user_prompt: str,
    toolbox: ToolBox,
    ceiling: Ceiling,
    on_call: Callable[[CallRecord], None] | None = None,
) -> EpisodeResult:
    meter = Meter(provider=provider, simulated=simulated, ceiling=ceiling, on_call=on_call)
    tools = tuple(
        Tool(name=s.name, description=s.description, parameters=s.parameters, _box=toolbox)
        for s in toolbox.specs
    )
    task = Task(
        prompt=user_prompt,
        system_prompt=system,
        tools=tools,
        max_tokens_per_call=ceiling.max_tokens_per_call,
        meter=meter,
    )

    def result(status: Any, reason: str | None = None, text: str = "") -> EpisodeResult:
        return EpisodeResult(
            status=status,
            reason=reason,
            turns=meter.turns,
            usage=meter.usage,
            calls=meter.calls,
            final_text=text,
        )

    try:
        text = agent.run(task)
    except StopEpisode as stop:
        return result(stop.status, stop.reason)
    if not simulated and meter.turns == 0:
        # A real agent that bills nothing did not record its calls: its cost would read as 0.
        return result("errored", "agent recorded no model calls")
    return result("completed", text=text)
