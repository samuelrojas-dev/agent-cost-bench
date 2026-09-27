"""Run the episode matrix: estimate the worst case, execute, resume, summarize."""

from __future__ import annotations

import functools
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from dowbench import __version__
from dowbench.agent.loop import CallRecord, run_episode
from dowbench.agent.tools import Injection, ToolBox
from dowbench.attacks.schema import Dataset
from dowbench.defenses import build_defenses
from dowbench.metering.pricing import ModelPrice, PriceTable
from dowbench.metrics import RunSummary, summarize
from dowbench.providers.base import Provider, ProviderSetupError
from dowbench.providers.mock import MockProvider
from dowbench.runner.budget import check_budget, spent
from dowbench.runner.config import RunConfig
from dowbench.runner.lock import RunLock
from dowbench.runner.matrix import EpisodeSpec, plan_episodes
from dowbench.runner.rate_limit import (
    RateLimitedProvider,
    RateLimiter,
    RatePlan,
    plan,
)
from dowbench.runner.replay import RecordingProvider
from dowbench.runner.store import EpisodeRecord, RunStore
from dowbench.sut import Agent, load_agent, run_agent_episode


class RunExistsError(RuntimeError):
    pass


class EpisodeErroredError(RuntimeError):
    """A billed call could not be priced; the run stops after recording it (ADR 0007)."""


class Estimate(BaseModel):
    episodes: int
    pending: int
    worst_case_tokens: int
    worst_case_usd: float | None  # None for unpriced runs: never invented (ADR 0008)
    max_model_calls: int
    counted_before_calls: bool = True  # False for agent runs: checked after calls (ADR 0012)
    simulated: bool
    rate_plan: RatePlan | None = None  # requests/minutes/days under the rate limit (ADR 0014)


class RunInfo(BaseModel):
    run_name: str
    dowbench_version: str
    git_commit: str | None
    provider: str
    simulated: bool
    # True when the run was replayed from a cassette: real recorded numbers, but not an
    # independent new result and never published as one (ADR 0015). Excludes simulated.
    replayed: bool = False
    episodes_planned: int
    config: RunConfig
    # Requested model -> versions the provider reported serving (filled in as calls return).
    served_model_versions: dict[str, list[str]] = {}
    # Everything this run directory has billed so far, across invocations (ADR 0010).
    spent_tokens: int = 0
    spent_usd: float | None = None


def build_provider(config: RunConfig, dataset: Dataset) -> Provider:
    if config.provider == "mock":
        return MockProvider([(a.marker, a.expected_signal) for a in dataset.attacks])
    if config.provider == "gemini":
        try:
            from dowbench.providers.gemini import GeminiProvider
        except ImportError as exc:
            raise ProviderSetupError(
                "the gemini provider needs its SDK: pip install 'dowbench[gemini]'"
            ) from exc
        return GeminiProvider()
    if config.provider == "anthropic":
        try:
            from dowbench.providers.claude import AnthropicProvider
        except ImportError as exc:
            raise ProviderSetupError(
                "the anthropic provider needs its SDK: pip install 'dowbench[anthropic]'"
            ) from exc
        return AnthropicProvider()
    raise ValueError(f"provider {config.provider!r} is not available yet")


def model_prices(config: RunConfig, prices: PriceTable) -> dict[str, ModelPrice]:
    """Price of each model, refusing ceilings that could reach a costlier tier (ADR 0005).

    Under ADR 0003 no single prompt can exceed ``ceiling.max_total_tokens``.
    """
    result = {m: prices.get(config.provider, m) for m in config.models}
    for price in result.values():
        price.check_prompt_limit(config.ceiling.max_total_tokens)
    return result


def estimate(
    config: RunConfig, specs: list[EpisodeSpec], prices: PriceTable, done: set[str]
) -> Estimate:
    """Worst case under the safety ceiling (ADR 0003): every pending episode spends it all."""
    pending = [s for s in specs if s.id not in done]
    # Agent runs are checked after each call, so the last call may overshoot the ceiling by
    # at most one call, itself assumed to be within the ceiling (ADR 0012).
    budget = config.ceiling.max_total_tokens * (2 if config.agent else 1)
    turns = config.ceiling.max_turns + (1 if config.agent else 0)
    worst_usd = None
    if not config.unpriced:
        per_token = {m: p.max_usd_per_token for m, p in model_prices(config, prices).items()}
        worst_usd = sum(budget * per_token[s.model] for s in pending)
    generate_calls = turns * len(pending)
    rate_plan = (
        plan(config.rate_limit, generate_calls=generate_calls, tokens=budget * len(pending))
        if config.rate_limit is not None
        else None
    )
    return Estimate(
        episodes=len(specs),
        pending=len(pending),
        worst_case_tokens=budget * len(pending),
        worst_case_usd=worst_usd,
        max_model_calls=generate_calls,
        counted_before_calls=config.agent is None,
        simulated=config.simulated,
        rate_plan=rate_plan,
    )


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def execute(
    config: RunConfig,
    dataset: Dataset,
    *,
    provider: Provider | None = None,
    agent: Agent | None = None,
    prices: PriceTable,
    out_dir: Path,
    resume: bool = True,
    on_episode: Callable[[int, int, EpisodeRecord], None] | None = None,
    budget_usd: float | None = None,
    budget_tokens: int | None = None,
) -> RunSummary:
    """Run pending episodes under the run-directory lock and cumulative budget (ADR 0010).

    The built-in loop needs a ``provider``; an agent run (``config.agent``, ADR 0012) uses
    ``agent``, loaded from the config when not given.
    """
    if config.agent is None:
        if provider is None or agent is not None:
            raise ValueError("a built-in run needs a provider and no agent")
    else:
        if provider is not None:
            raise ValueError("an agent run calls models through the agent, not a provider")
        agent = agent or load_agent(config.agent)
    store = RunStore(out_dir / config.run_name)
    with RunLock(store.run_dir):
        return _execute_locked(
            config,
            dataset,
            store,
            provider,
            agent,
            prices,
            resume,
            on_episode,
            budget_usd,
            budget_tokens,
        )


def _execute_locked(
    config: RunConfig,
    dataset: Dataset,
    store: RunStore,
    provider: Provider | None,
    agent: Agent | None,
    prices: PriceTable,
    resume: bool,
    on_episode: Callable[[int, int, EpisodeRecord], None] | None,
    budget_usd: float | None,
    budget_tokens: int | None,
) -> RunSummary:
    specs = plan_episodes(config, dataset)
    existing = store.load_episodes()
    if existing and not resume:
        raise RunExistsError(f"{store.run_dir} already has episodes; resume or rename the run")
    done = {r.episode_id for r in existing}
    price_of = {} if config.unpriced else model_prices(config, prices)
    # Replay reads recorded responses: it makes no call, spends nothing, and is not rate
    # limited or re-recorded (ADR 0015).
    replaying = provider is not None and getattr(provider, "replaying", False)
    already = spent(store, config, prices)
    if not replaying:
        # Checked here, under the lock, so no caller can skip it and no parallel run races it.
        worst = estimate(config, specs, prices, done)
        check_budget(
            worst_tokens=worst.worst_case_tokens,
            worst_usd=worst.worst_case_usd,
            already=already,
            budget_usd=budget_usd,
            budget_tokens=budget_tokens,
            simulated=config.simulated,
        )

    # Rate limiting applies to real providers; the mock has no quota (ADR 0014). Replay makes
    # no network call, so it is never rate limited: wrapping it would make it sleep on real
    # time reproducing a recorded run (ADR 0015).
    limiter = None
    if (
        config.rate_limit is not None
        and provider is not None
        and not provider.simulated
        and not replaying
    ):
        limiter = RateLimiter(config.rate_limit)
        provider = RateLimitedProvider(provider, limiter)
    # Every run records a cassette so it can be replayed offline later; a replayed run keeps
    # the source's simulated flag, so a mock cassette stays SIMULATED and is never taken for a
    # real result (ADR 0015).
    if provider is not None and not replaying:
        provider = RecordingProvider(provider, store)

    info = RunInfo(
        run_name=config.run_name,
        dowbench_version=__version__,
        git_commit=_git_commit(),
        provider=provider.name if provider else config.provider,
        simulated=provider.simulated if provider else config.simulated,
        replayed=replaying,
        episodes_planned=len(specs),
        config=config,
        served_model_versions=store.served_model_versions(),
        spent_tokens=already.tokens,
        spent_usd=already.usd,
    )
    store.write_json(store.run_path, info)
    try:
        _run_pending(
            config, dataset, specs, done, provider, agent, store, price_of, on_episode, limiter
        )
    finally:
        # Even when a run stops early, run.json says which model versions actually served.
        info.served_model_versions = store.served_model_versions()
        final = spent(store, config, prices)
        info.spent_tokens, info.spent_usd = final.tokens, final.usd
        store.write_json(store.run_path, info)

    planned = {s.id for s in specs}
    records = [r for r in store.load_episodes() if r.episode_id in planned]
    summary = summarize(
        records,
        run_name=config.run_name,
        baseline_defense=config.baseline_label,
        threshold=config.success_threshold,
    )
    store.write_json(store.summary_path, summary)
    return summary


def _run_pending(
    config: RunConfig,
    dataset: Dataset,
    specs: list[EpisodeSpec],
    done: set[str],
    provider: Provider | None,
    agent: Agent | None,
    store: RunStore,
    price_of: dict[str, ModelPrice],
    on_episode: Callable[[int, int, EpisodeRecord], None] | None,
    limiter: RateLimiter | None,
) -> None:
    pending = [s for s in specs if s.id not in done]
    # Worst-case requests for one episode: each of its calls is a generate + a countTokens.
    turns = config.ceiling.max_turns + (1 if config.agent else 0)
    per_episode_requests = turns * 2
    for index, spec in enumerate(pending, start=1):
        if limiter is not None:
            limiter.reserve_episode(per_episode_requests)  # stop between episodes on RPD
        attempt = uuid.uuid4().hex
        # Recording and replay key their calls by episode and attempt (ADR 0015).
        begin = getattr(provider, "begin_episode", None)
        if begin is not None:
            begin(spec.id, attempt)
        context = {"episode_id": spec.id, "model": spec.model, "attempt": attempt}
        record = _run_one(
            config, dataset, spec, provider, agent, functools.partial(store.append_call, context)
        )
        record = record.model_copy(
            update={
                "cost_usd": (price_of[spec.model].cost_usd(record.usage) if price_of else None),
                "attempt": attempt,
            }
        )
        store.append_episode(record)
        if on_episode is not None:
            on_episode(index, len(pending), record)
        if record.status == "errored":
            raise EpisodeErroredError(
                f"episode {spec.id} stopped: {record.reason}. Its spend is recorded; "
                "resume skips it. Fix the cause before resuming."
            )


def _run_one(
    config: RunConfig,
    dataset: Dataset,
    spec: EpisodeSpec,
    provider: Provider | None,
    agent: Agent | None,
    on_call: Callable[[CallRecord], None],
) -> EpisodeRecord:
    task = dataset.benign_task(spec.benign_task_id)
    prompt = task.prompt
    injection = None
    if spec.kind == "attack":
        attack = dataset.attack(spec.item_id)
        if attack.vector == "user_prompt":
            prompt = f"{prompt}\n\n{attack.rendered_payload}"
        else:
            assert attack.target_tool is not None  # guaranteed by the schema
            if attack.vector == "tool_description":
                injection = Injection(
                    attack.target_tool, attack.rendered_payload, where="description"
                )
            else:
                injection = Injection(
                    attack.target_tool, attack.render, relay_tool=attack.relay_tool
                )

    toolbox = ToolBox(task.tool, injection)
    if agent is not None:
        simulated = config.simulated
        result = run_agent_episode(
            agent,
            provider=config.provider,
            simulated=simulated,
            system=config.system_prompt,
            user_prompt=prompt,
            toolbox=toolbox,
            ceiling=config.ceiling,
            on_call=on_call,
        )
    else:
        assert provider is not None  # checked by execute()
        simulated = provider.simulated
        result = run_episode(
            provider,
            model=spec.model,
            system=config.system_prompt,
            user_prompt=prompt,
            toolbox=toolbox,
            defenses=build_defenses(spec.defense),
            ceiling=config.ceiling,
            on_call=on_call,
        )
    return EpisodeRecord(
        episode_id=spec.id,
        model=spec.model,
        defense=spec.defense.display,
        kind=spec.kind,
        item_id=spec.item_id,
        benign_task_id=spec.benign_task_id,
        repeat=spec.repeat,
        status=result.status,
        reason=result.reason,
        turns=result.turns,
        usage=result.usage,
        cost_usd=0.0,
        simulated=simulated,
    )
