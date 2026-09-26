"""Command-line interface: list, estimate, run, report."""

from __future__ import annotations

import math
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
from dowbench.report import load as load_report
from dowbench.report import render as render_report
from dowbench.runner.budget import BudgetError, Spend, check_budget, spent
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import (
    EpisodeErroredError,
    Estimate,
    RunExistsError,
    build_provider,
    estimate,
    execute,
)
from dowbench.runner.lock import RunLockedError
from dowbench.runner.matrix import plan_episodes
from dowbench.runner.store import RunStore
from dowbench.sut import load_agent

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


def _print_spent(already: Spend) -> None:
    usd = "" if already.usd is None else f", ${already.usd:.4f}"
    typer.echo(f"already spent in this run dir: {already.tokens:,} tokens{usd}")


def _print_estimate(result: Estimate) -> None:
    usd = (
        "USD not computed (unpriced)"
        if result.worst_case_usd is None
        else f"${result.worst_case_usd:.4f}"
    )
    typer.echo(
        f"episodes: {result.episodes} (pending {result.pending})\n"
        f"worst case: {result.worst_case_tokens:,} tokens, {usd}\n"
        f"model calls: at most {result.max_model_calls}, "
        + (
            "each preceded by one token count"
            if result.counted_before_calls
            else "made by the agent and checked after each call"
        )
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
    store = RunStore(out / config.run_name)
    done = {r.episode_id for r in store.load_episodes()}
    prices = PriceTable.load()
    try:
        _print_estimate(estimate(config, specs, prices, done))
        _print_spent(spent(store, config, prices))
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
        typer.Option(
            "--budget-usd",
            help="Total USD cap for this run dir: spent so far + worst case must fit",
        ),
    ] = None,
    budget_tokens: Annotated[
        int | None,
        typer.Option(
            "--budget-tokens",
            min=0,
            help="Total token cap for this run dir: spent + worst case (required unpriced)",
        ),
    ] = None,
    resume: Annotated[bool, typer.Option(help="Skip episodes already in the run dir")] = True,
) -> None:
    """Run the episode matrix and write calls.jsonl, episodes.jsonl and summary.json."""
    if budget_usd is not None and not (math.isfinite(budget_usd) and budget_usd >= 0):
        typer.echo(
            f"--budget-usd must be a finite, non-negative number, got {budget_usd}", err=True
        )
        raise typer.Exit(2)
    config = _load_config(config_path, provider)
    dataset = load_dataset()
    prices = PriceTable.load()
    specs = plan_episodes(config, dataset)
    store = RunStore(out / config.run_name)
    done = {r.episode_id for r in store.load_episodes()} if resume else set()
    try:
        worst = estimate(config, specs, prices, done)
        already = spent(store, config, prices)
    except PricingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    _print_estimate(worst)
    _print_spent(already)
    try:
        # Early, friendly refusal before building a provider; execute() re-checks under the
        # run-directory lock, which is the check that cannot be skipped (ADR 0010).
        check_budget(
            worst_tokens=worst.worst_case_tokens,
            worst_usd=worst.worst_case_usd,
            already=already,
            budget_usd=budget_usd,
            budget_tokens=budget_tokens,
            simulated=config.simulated,
        )
    except BudgetError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None

    def progress(index: int, total: int, record: object) -> None:
        typer.echo(f"\r{index}/{total} episodes", nl=index == total)

    llm, agent = None, None
    try:
        if config.agent is None:
            llm = build_provider(config, dataset)
        else:
            # The agent calls its model itself; dowbench only meters it (ADR 0012).
            agent = load_agent(config.agent)
    except (ProviderSetupError, ImportError, AttributeError, TypeError, ValueError) as exc:
        typer.echo(f"cannot set up the run: {exc}", err=True)
        raise typer.Exit(2) from None
    try:
        summary = execute(
            config,
            dataset,
            provider=llm,
            agent=agent,
            prices=prices,
            out_dir=out,
            resume=resume,
            on_episode=progress,
            budget_usd=budget_usd,
            budget_tokens=budget_tokens,
        )
    except (RunExistsError, RunLockedError, BudgetError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    except EpisodeErroredError as exc:
        typer.echo(f"{exc}\nresults so far: {out / config.run_name}", err=True)
        raise typer.Exit(1) from None
    _print_summary(summary)
    typer.echo(f"results: {out / config.run_name}")


@app.command("report")
def report_cmd(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="A run directory")],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Write Markdown here, not stdout")
    ] = None,
) -> None:
    """Render run.json and summary.json as a Markdown report. Makes no calls."""
    try:
        text = render_report(*load_report(run_dir))
    except (FileNotFoundError, ValidationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"report: {output}")
