import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dowbench.attacks.schema import load_dataset
from dowbench.cli import app
from dowbench.metering.pricing import PriceTable
from dowbench.report import SIMULATED_BANNER, _success, load, render
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import build_provider, execute

PILOT = Path(__file__).parent.parent / "configs" / "pilot.yaml"


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("runs")
    config, dataset = RunConfig.from_yaml(PILOT), load_dataset()
    execute(
        config,
        dataset,
        provider=build_provider(config, dataset),
        prices=PriceTable.load(),
        out_dir=out,
    )
    return out / config.run_name


def test_report_is_deterministic_and_complete(run_dir: Path) -> None:
    info, summary, tool_calls = load(run_dir)
    text = render(info, summary, tool_calls)
    assert text == render(*load(run_dir))  # same files, same report
    assert text.startswith(f"# Results: {summary.run_name}\n")
    assert SIMULATED_BANNER in text
    assert "`mock-1` (served: `mock-1-simulated`)" in text
    for d in summary.defenses:
        assert f"| `{d.model}` | `{d.defense}` |" in text
    attack_rows = [line for line in text.splitlines() if line.startswith("| `mock-1` | `")]
    assert len(attack_rows) == len(summary.defenses) + len(summary.attacks)


def test_attack_episodes_table_reports_tool_calls(run_dir: Path) -> None:
    info, summary, tool_calls = load(run_dir)
    text = render(info, summary, tool_calls)
    assert "| Tool calls |" in text  # the column is present in the Attack episodes header
    # At least one attack episode made tool calls, and the report shows that real count, not n/a.
    assert any(count > 0 for count in tool_calls.values())
    busiest = max(summary.attacks, key=lambda a: tool_calls.get(a.episode_id, 0))
    assert f"| {tool_calls[busiest.episode_id]} | {_success(busiest.success)} |" in text
    # Without the map, the column is honest about not knowing rather than inventing a count.
    assert "| n/a | " in render(info, summary)


def test_reproduce_section_holds_the_exact_config(run_dir: Path) -> None:
    info, summary, tool_calls = load(run_dir)
    text = render(info, summary, tool_calls)
    block = text.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert RunConfig.model_validate(json.loads(block)) == info.config


def test_real_runs_have_no_banner_and_unpriced_runs_no_usd(run_dir: Path) -> None:
    info, summary, tool_calls = load(run_dir)
    real_info = info.model_copy(
        update={
            "simulated": False,
            "served_model_versions": {},
            "spent_usd": None,
            "config": info.config.model_copy(update={"unpriced": True}),
        }
    )
    text = render(real_info, summary.model_copy(update={"simulated": False}), tool_calls)
    assert SIMULATED_BANNER not in text
    assert "served version not recorded" in text
    assert f"| Billed in this run dir | {info.spent_tokens:,} tokens |" in text
    assert "not applied (unpriced run)" in text


def test_missing_files_are_reported(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"run\.json"):
        load(tmp_path)


def test_cli_writes_the_report_to_a_file(run_dir: Path, tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    result = CliRunner().invoke(app, ["report", str(run_dir), "--output", str(target)])
    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == render(*load(run_dir))


def test_cli_refuses_a_directory_without_results(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["report", str(tmp_path)])
    assert result.exit_code == 2
    assert "run `dowbench run` first" in result.output
