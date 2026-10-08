"""Render a run directory as a Markdown report (ADR 0011).

The report is a pure function of `run.json` and `summary.json`: no clock, no network, so
the same run directory always renders the same text and anyone can regenerate it.
Simulated runs carry a banner on top and cannot be mistaken for results.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from dowbench.metrics import RunSummary
from dowbench.runner.execute import RunInfo
from dowbench.runner.store import RunStore

SIMULATED_BANNER = (
    "> **SIMULATED — not a result.** These numbers come from the mock provider. They show "
    "that the pipeline works and must not be published or compared with real runs."
)

REPLAYED_BANNER = (
    "> **REPLAYED — not an independent result.** These numbers were replayed from a prior "
    "real run's cassette (ADR 0015). They are real recorded data, but replaying does not "
    "produce a new result and must not be counted as one."
)


def _num(value: float | None, pattern: str) -> str:
    return "n/a" if value is None else pattern.format(value)


def _success(value: bool | None) -> str:
    return {True: "yes", False: "no", None: "undetermined"}[value]


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def load(run_dir: Path) -> tuple[RunInfo, RunSummary, dict[str, int]]:
    store = RunStore(run_dir)
    for path in (store.run_path, store.summary_path):
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run `dowbench run` first")
    info = RunInfo.model_validate_json(store.run_path.read_text(encoding="utf-8"))
    summary = RunSummary.model_validate_json(store.summary_path.read_text(encoding="utf-8"))
    # Tool-call counts live in episodes.jsonl, not summary.json, so the report can show them
    # without changing the summary bytes the published cassette hashes cover (ADR 0024).
    tool_calls = {e.episode_id: e.tool_calls for e in store.load_episodes()}
    return info, summary, tool_calls


def render(
    info: RunInfo, summary: RunSummary, tool_calls_by_episode: Mapping[str, int] | None = None
) -> str:
    config = info.config
    ceiling = config.ceiling
    models = ", ".join(
        f"`{m}` (served: {', '.join(f'`{v}`' for v in info.served_model_versions.get(m, []))})"
        if info.served_model_versions.get(m)
        else f"`{m}` (served version not recorded)"
        for m in config.models
    )
    spent = f"{info.spent_tokens:,} tokens" + (
        "" if info.spent_usd is None else f", ${info.spent_usd:.4f}"
    )
    lines = [f"# Results: {summary.run_name}", ""]
    if summary.simulated or info.simulated:
        lines += [SIMULATED_BANNER, ""]
    elif info.replayed:
        lines += [REPLAYED_BANNER, ""]
    lines += _table(
        ["", ""],
        [
            ["Provider", f"`{info.provider}`"],
            ["Agent under test", f"`{config.agent}`" if config.agent else "built-in loop"],
            ["Models", models],
            ["dowbench", f"{info.dowbench_version}, commit `{info.git_commit or 'unknown'}`"],
            ["Episodes planned", str(info.episodes_planned)],
            [
                "Safety ceiling",
                f"{ceiling.max_turns} turns, {ceiling.max_tokens_per_call:,} tokens per call, "
                f"{ceiling.max_total_tokens:,} tokens per episode",
            ],
            ["Success", f"amplification A ≥ {summary.success_threshold:g}"],
            ["Baseline", f"benign tasks under `{summary.baseline_defense}`"],
            ["Billed in this run dir", spent],
            ["Prices", "not applied (unpriced run)" if config.unpriced else "from `pricing.yaml`"],
        ],
    )
    lines += ["", "## Defenses", ""]
    lines += _table(
        [
            "Model",
            "Defense",
            "ASR",
            "95% CI",
            "Decided / attacks",
            "Median A",
            "Censored",
            "Errored",
            "Benign completed",
            "Benign overhead",
        ],
        [
            [
                f"`{d.model}`",
                f"`{d.defense}`",
                _num(d.asr, "{:.0%}"),
                f"{d.asr_ci95[0]:.2f}-{d.asr_ci95[1]:.2f}" if d.asr_ci95 else "n/a",
                f"{d.attack_episodes - d.undetermined} / {d.attack_episodes}",
                _num(d.median_amplification, "{:.1f}x"),
                str(d.censored),
                str(d.errored),
                _num(d.benign_completion_rate, "{:.0%}"),
                _num(d.benign_overhead, "{:+.0%}"),
            ]
            for d in summary.defenses
        ],
    )
    calls_by_episode = tool_calls_by_episode or {}
    lines += ["", "## Attack episodes", ""]
    lines += _table(
        ["Model", "Defense", "Attack", "Repeat", "Status", "A", "Tool calls", "Success"],
        [
            [
                f"`{a.model}`",
                f"`{a.defense}`",
                f"`{a.attack_id}`",
                str(a.repeat),
                a.status,
                _num(a.amplification, "{:.1f}x"),
                str(calls_by_episode[a.episode_id]) if a.episode_id in calls_by_episode else "n/a",
                _success(a.success),
            ]
            for a in sorted(summary.attacks, key=lambda a: (a.model, a.defense, a.attack_id))
        ],
    )
    lines += [
        "",
        "## Reproduce",
        "",
        f"Check out commit `{info.git_commit or 'unknown'}`, save the config below as "
        "`config.yaml` (JSON is valid YAML), then run `dowbench estimate config.yaml` and "
        "`dowbench run config.yaml` with a budget. Metrics can be recomputed from "
        "`calls.jsonl` and `episodes.jsonl` without new calls.",
        "",
        "```json",
        config.model_dump_json(indent=2),
        "```",
        "",
    ]
    return "\n".join(lines)
