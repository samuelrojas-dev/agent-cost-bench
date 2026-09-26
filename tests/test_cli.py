import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dowbench.cli import app

PILOT = str(Path(__file__).parent.parent / "configs" / "pilot.yaml")
runner = CliRunner()


def test_list_attacks_shows_sources() -> None:
    result = runner.invoke(app, ["list", "attacks"])
    assert result.exit_code == 0
    assert "loop-identical-001" in result.output
    assert "arXiv:2601.10955" in result.output


def test_list_defenses_includes_baseline() -> None:
    result = runner.invoke(app, ["list", "defenses"])
    assert result.exit_code == 0
    assert "none" in result.output
    assert "loop_detect" in result.output


def test_estimate_prints_worst_case_and_simulated_notice(tmp_path: Path) -> None:
    result = runner.invoke(app, ["estimate", PILOT, "--out", str(tmp_path)])
    assert result.exit_code == 0
    assert "worst case: 2,000,000 tokens" in result.output
    assert "SIMULATED" in result.output


def test_run_with_mock_provider_writes_results(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", PILOT, "--provider", "mock", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "loop_detect" in result.output
    assert "SIMULATED" in result.output
    summary = json.loads((tmp_path / "pilot-mock" / "summary.json").read_text())
    assert summary["simulated"] is True


def test_run_refuses_when_worst_case_exceeds_budget(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", PILOT, "--out", str(tmp_path), "--budget-usd", "0.01"])
    assert result.exit_code == 2
    assert "exceeds budget" in result.output
    assert not (tmp_path / "pilot-mock" / "episodes.jsonl").exists()


def test_run_rejects_unavailable_provider(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", PILOT, "--provider", "nope", "--out", str(tmp_path)])
    assert result.exit_code == 2
    assert "not available yet" in result.output


@pytest.mark.parametrize("budget", ["nan", "-nan", "inf", "-1"])
def test_run_rejects_non_finite_or_negative_budget(tmp_path: Path, budget: str) -> None:
    result = runner.invoke(app, ["run", PILOT, "--out", str(tmp_path), "--budget-usd", budget])
    assert result.exit_code == 2
    assert "finite, non-negative" in result.output
    assert not (tmp_path / "pilot-mock").exists()


def _unpriced_real_config(tmp_path: Path) -> str:
    path = tmp_path / "smoke.yaml"
    path.write_text(
        "run_name: smoke\nprovider: gemini\nmodels: [some-free-model]\nunpriced: true\n"
        "attacks: []\nbenign_tasks: [b-refund-policy]\ndefenses: [{name: none}]\n"
        "ceiling: {max_turns: 2, max_tokens_per_call: 128, max_total_tokens: 400}\n"
    )
    return str(path)


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        ([], "require --budget-tokens"),
        (["--budget-usd", "1"], "cannot be checked in USD"),
        (["--budget-tokens", "399"], "exceeds budget"),
    ],
)
def test_unpriced_real_run_is_capped_in_tokens(
    tmp_path: Path, flags: list[str], message: str
) -> None:
    config = _unpriced_real_config(tmp_path)
    result = runner.invoke(app, ["run", config, "--out", str(tmp_path), *flags])
    assert result.exit_code == 2
    assert message in result.output
    assert "USD not computed" in result.output
    assert not (tmp_path / "smoke").exists()  # refused before building the provider


def test_estimate_shows_what_the_run_dir_already_spent(tmp_path: Path) -> None:
    runner.invoke(app, ["run", PILOT, "--out", str(tmp_path)])
    result = runner.invoke(app, ["estimate", PILOT, "--out", str(tmp_path)])
    assert result.exit_code == 0
    assert "already spent in this run dir:" in result.output
    assert "already spent in this run dir: 0 tokens" not in result.output


def test_run_refuses_a_locked_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "pilot-mock"
    run_dir.mkdir()
    (run_dir / "run.lock").write_text('{"pid": 1, "host": "h", "started": "t"}')
    result = runner.invoke(app, ["run", PILOT, "--out", str(tmp_path)])
    assert result.exit_code == 2
    assert "another run may be using this directory" in result.output
    assert not (run_dir / "calls.jsonl").exists()
