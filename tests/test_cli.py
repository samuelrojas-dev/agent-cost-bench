import json
from pathlib import Path

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
