"""Run the episode matrix: estimate the worst case, execute, resume, summarize."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from dowbench import __version__
from dowbench.agent.loop import CallRecord, run_episode
from dowbench.agent.tools import Injection, ToolBox
from dowbench.attacks.schema import Dataset
from dowbench.defenses import build_defenses
from dowbench.metering.pricing import PriceTable
from dowbench.metrics import RunSummary, summarize
from dowbench.providers.anthropic_api import AnthropicProvider
from dowbench.providers.base import Provider
from dowbench.providers.gemini_api import GeminiProvider
from dowbench.providers.mock import MockProvider
from dowbench.runner.config import RunConfig
from dowbench.runner.matrix import EpisodeSpec, plan_episodes
from dowbench.runner.store import EpisodeRecord, RunStore


class RunExistsError(RuntimeError):
    pass


class Estimate(BaseModel):
    episodes: int
    pending: int
    worst_case_tokens: int
    worst_case_usd: float
    simulated: bool


class RunInfo(BaseModel):
    run_name: str
    dowbench_version: str
    git_commit: str | None
    provider: str
    simulated: bool
    episodes_planned: int
    config: RunConfig


def build_provider(config: RunConfig, dataset: Dataset) -> Provider:
    """Real providers read their API key from the environment (see .env.example)."""
    if config.provider == "mock":
        return MockProvider([(a.payload, a.expected_signal) for a in dataset.attacks])
    if config.provider == "anthropic":
        return AnthropicProvider()
    if config.provider == "gemini":
        return GeminiProvider()
    raise ValueError(f"provider {config.provider!r} is not available")


def estimate(
    config: RunConfig, specs: list[EpisodeSpec], prices: PriceTable, done: set[str]
) -> Estimate:
    """Worst case under the safety ceiling (ADR 0003): every pending episode spends it all."""
    pending = [s for s in specs if s.id not in done]
    per_token = {m: prices.get(config.provider, m).max_usd_per_token for m in config.models}
    budget = config.ceiling.max_total_tokens
    return Estimate(
        episodes=len(specs),
        pending=len(pending),
        worst_case_tokens=budget * len(pending),
        worst_case_usd=sum(budget * per_token[s.model] for s in pending),
        simulated=config.simulated,
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
    provider: Provider,
    prices: PriceTable,
    out_dir: Path,
    resume: bool = True,
    on_episode: Callable[[int, int, EpisodeRecord], None] | None = None,
) -> RunSummary:
    store = RunStore(out_dir / config.run_name)
    specs = plan_episodes(config, dataset)
    existing = store.load_episodes()
    if existing and not resume:
        raise RunExistsError(f"{store.run_dir} already has episodes; resume or rename the run")
    done = {r.episode_id for r in existing}
    model_prices = {m: prices.get(config.provider, m) for m in config.models}

    store.write_json(
        store.run_path,
        RunInfo(
            run_name=config.run_name,
            dowbench_version=__version__,
            git_commit=_git_commit(),
            provider=provider.name,
            simulated=provider.simulated,
            episodes_planned=len(specs),
            config=config,
        ),
    )

    pending = [s for s in specs if s.id not in done]
    for index, spec in enumerate(pending, start=1):
        record, calls = _run_one(config, dataset, spec, provider)
        record = record.model_copy(
            update={"cost_usd": model_prices[spec.model].cost_usd(record.usage)}
        )
        store.append(record, calls)
        if on_episode is not None:
            on_episode(index, len(pending), record)

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


def _run_one(
    config: RunConfig, dataset: Dataset, spec: EpisodeSpec, provider: Provider
) -> tuple[EpisodeRecord, list[CallRecord]]:
    task = dataset.benign_task(spec.benign_task_id)
    prompt = task.prompt
    injection = None
    if spec.kind == "attack":
        attack = dataset.attack(spec.item_id)
        if attack.vector == "user_prompt":
            prompt = f"{prompt}\n\n{attack.rendered_payload}"
        else:
            assert attack.target_tool is not None  # guaranteed by the schema
            injection = Injection(attack.target_tool, attack.rendered_payload)

    result = run_episode(
        provider,
        model=spec.model,
        system=config.system_prompt,
        user_prompt=prompt,
        toolbox=ToolBox(task.tool, injection),
        defenses=build_defenses(spec.defense),
        ceiling=config.ceiling,
    )
    record = EpisodeRecord(
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
        simulated=provider.simulated,
    )
    return record, result.calls
