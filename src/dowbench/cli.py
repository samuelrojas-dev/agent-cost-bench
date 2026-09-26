"""Command-line interface: list, estimate, run."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from dowbench.attacks.schema import load_dataset
from dowbench.defenses import NO_DEFENSE, REGISTRY
from dowbench.metering.pricing import PriceTable, PricingError
from dowbench.metrics import RunSummary
from dowbench.providers.base import ProviderSetupError
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import (
    Estimate,
    RunExistsError,
    build_provider,
    estimate,
    execute,
)
from dowbench.runner.matrix import plan_episodes
from dowbench.runner.store import RunStore

app = typer.Typer(no_args_is_help=True, help="Denial-of-wallet benchmark for LLM agents.")

SIMULATED_NOTICE = "SIMULATED: mock provider numbers. Not a real result; never publish them."

ConfigArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Run config YAML")]
OutOption = Annotated[Path, typer.Option("--out", help="Directory for raw results")]
ProviderOption = Annotated[
    str | None, typer.Option("--provider", help="Override the config's provider")
]


class Listable(StrEnum):
    attacks = "attacks"
    benign = "benign"
    defenses = "defenses"


def _load_config(path: Path, provider: str | None) -> RunConfig:
    try:
        config = RunConfig.from_yaml(path)
        if provider is not None:
            config = RunConfig.model_validate({**config.model_dump(), "provider": provider})
    except ValidationError as exc:
        typer.echo(f"invalid config {path}:\n{exc}", err=True)
        raise typer.Exit(2) from None
    return config


def _print_estimate(result: Estimate) -> None:
    typer.echo(
        f"episodes: {result.episodes} (pending {result.pending})\n"
        f"worst case: {result.worst_case_tokens:,} tokens, ${result.worst_case_usd:.4f}"
    )
    if result.simulated:
        typer.echo(SIMULATED_NOTICE)


def _print_summary(summary: RunSummary) -> None:
    def fmt(value: float | None, pattern: str) -> str:
        return "n/a" if value is None else pattern.format(value)

    typer.echo(f"\nrun {summary.run_name}  threshold A >= {summary.success_threshold:g}")
    header = f"{'model':<12} {'defense':<14} {'ASR':>6} {'95% CI':>13} {'median A':>9} "
    typer.echo(header + f"{'censored':>8} {'benign ok':>9} {'overhead':>9}")
    for s in summary.defenses:
        ci = f"{s.asr_ci95[0]:.2f}-{s.asr_ci95[1]:.2f}" if s.asr_ci95 else "n/a"
        typer.echo(
            f"{s.model:<12} {s.defense:<14} {fmt(s.asr, '{:.0%}'):>6} {ci:>13} "
            f"{fmt(s.median_amplification, '{:.1f}x'):>9} {s.censored:>8} "
            f"{fmt(s.benign_completion_rate, '{:.0%}'):>9} "
            f"{fmt(s.benign_overhead, '{:+.0%}'):>9}"
        )
    if summary.simulated:
        typer.echo(SIMULATED_NOTICE)


@app.command("list")
def list_items(what: Annotated[Listable, typer.Argument(help="What to list")]) -> None:
    """List the attacks, benign tasks or defenses available."""
    dataset = load_dataset()
    if what is Listable.attacks:
        for attack in dataset.attacks:
            typer.echo(f"{attack.id:<22} {attack.family:<15} {attack.vector:<12} {attack.source}")
    elif what is Listable.benign:
        for task in dataset.benign:
            typer.echo(f"{task.id:<20} {task.tool:<10} {task.prompt}")
    else:
        typer.echo(f"{NO_DEFENSE:<14} baseline: no defense, safety ceiling only")
        for name, cls in REGISTRY.items():
            doc = (cls.__doc__ or "").strip().splitlines()[0]
            typer.echo(f"{name:<14} {doc}")


@app.command("estimate")
def estimate_cmd(
    config_path: ConfigArg,
    out: OutOption = Path("results/raw"),
    provider: ProviderOption = None,
) -> None:
    """Worst-case tokens and USD for the pending episodes, without calling any model."""
    config = _load_config(config_path, provider)
    dataset = load_dataset()
    specs = plan_episodes(config, dataset)
    done = {r.episode_id for r in RunStore(out / config.run_name).load_episodes()}
    try:
        _print_estimate(estimate(config, specs, PriceTable.load(), done))
    except PricingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None


@app.command("run")
def run_cmd(
    config_path: ConfigArg,
    out: OutOption = Path("results/raw"),
    provider: ProviderOption = None,
    budget_usd: Annotated[
        float | None,
        typer.Option("--budget-usd", help="Refuse to start if the worst case exceeds this"),
    ] = None,
    resume: Annotated[bool, typer.Option(help="Skip episodes already in the run dir")] = True,
) -> None:
    """Run the episode matrix and write calls.jsonl, episodes.jsonl and summary.json."""
    config = _load_config(config_path, provider)
    dataset = load_dataset()
    prices = PriceTable.load()
    specs = plan_episodes(config, dataset)
    done = (
        {r.episode_id for r in RunStore(out / config.run_name).load_episodes()} if resume else set()
    )
    try:
        worst = estimate(config, specs, prices, done)
    except PricingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    _print_estimate(worst)
    if not config.simulated and budget_usd is None:
        typer.echo("real providers require --budget-usd", err=True)
        raise typer.Exit(2)
    if budget_usd is not None and worst.worst_case_usd > budget_usd:
        typer.echo(
            f"worst case ${worst.worst_case_usd:.4f} exceeds budget ${budget_usd:.4f}; "
            "lower the ceiling, the matrix, or raise the budget",
            err=True,
        )
        raise typer.Exit(2)

    def progress(index: int, total: int, record: object) -> None:
        typer.echo(f"\r{index}/{total} episodes", nl=index == total)

    try:
        llm = build_provider(config, dataset)
    except ProviderSetupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    try:
        summary = execute(
            config,
            dataset,
            provider=llm,
            prices=prices,
            out_dir=out,
            resume=resume,
            on_episode=progress,
        )
    except RunExistsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    _print_summary(summary)
    typer.echo(f"results: {out / config.run_name}")
