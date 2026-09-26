import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dowbench.attacks.schema import load_dataset
from dowbench.metering.pricing import PriceTable
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import RunExistsError, build_provider, estimate, execute
from dowbench.runner.matrix import plan_episodes

PILOT = Path(__file__).parent.parent / "configs" / "pilot.yaml"


def _config(**overrides: Any) -> RunConfig:
    data = RunConfig.from_yaml(PILOT).model_dump(mode="json")
    data.update(overrides)
    return RunConfig.model_validate(data)


def _execute(config: RunConfig, out: Path, *, resume: bool = True) -> Any:
    dataset = load_dataset()
    return execute(
        config,
        dataset,
        provider=build_provider(config, dataset),
        prices=PriceTable.load(),
        out_dir=out,
        resume=resume,
    )


def test_pilot_config_is_valid() -> None:
    config = RunConfig.from_yaml(PILOT)
    assert config.baseline_label == "none"
    assert config.simulated


def test_config_requires_exactly_one_baseline() -> None:
    with pytest.raises(ValidationError, match="must be 'none'"):
        _config(defenses=[{"name": "turn_limit"}])


def test_config_rejects_unavailable_provider() -> None:
    with pytest.raises(ValidationError, match="not available yet"):
        _config(provider="openai")


def test_episode_ids_are_stable_and_sensitive_to_config() -> None:
    dataset = load_dataset()
    first = [s.id for s in plan_episodes(_config(), dataset)]
    assert first == [s.id for s in plan_episodes(_config(), dataset)]
    assert len(set(first)) == len(first)
    changed = [s.id for s in plan_episodes(_config(system_prompt="other"), dataset)]
    assert not set(first) & set(changed)


def test_plan_has_benign_and_attack_episodes_per_defense() -> None:
    specs = plan_episodes(_config(repeats=2), load_dataset())
    # 5 benign + 5 attacks, 4 defenses, 2 repeats
    assert len(specs) == 10 * 4 * 2


def test_unknown_attack_id_rejected() -> None:
    with pytest.raises(ValueError, match="unknown attack ids"):
        plan_episodes(_config(attacks=["nope"]), load_dataset())


def test_estimate_is_ceiling_times_pending_episodes() -> None:
    config = _config()
    specs = plan_episodes(config, load_dataset())
    result = estimate(config, specs, PriceTable.load(), done={specs[0].id})
    assert result.pending == len(specs) - 1
    assert result.worst_case_tokens == 50_000 * (len(specs) - 1)
    assert result.worst_case_usd == pytest.approx(result.worst_case_tokens * 5.0 / 1_000_000)


def test_pilot_run_writes_calls_episodes_and_summary(tmp_path: Path) -> None:
    summary = _execute(_config(), tmp_path)
    run_dir = tmp_path / "pilot-mock"

    episodes = [json.loads(line) for line in (run_dir / "episodes.jsonl").read_text().splitlines()]
    calls = [json.loads(line) for line in (run_dir / "calls.jsonl").read_text().splitlines()]
    assert len(episodes) == 40
    assert len(calls) == sum(e["turns"] for e in episodes)
    assert {c["episode_id"] for c in calls} <= {e["episode_id"] for e in episodes}

    assert summary.simulated
    assert len(summary.attacks) == 20
    assert all(o.amplification is not None for o in summary.attacks)
    assert json.loads((run_dir / "summary.json").read_text())["run_name"] == "pilot-mock"
    assert json.loads((run_dir / "run.json").read_text())["simulated"] is True


def test_undefended_mock_attacks_amplify_and_loop_detect_blocks_loops(tmp_path: Path) -> None:
    summary = _execute(_config(), tmp_path)
    outcomes = {(o.defense, o.attack_id): o for o in summary.attacks}
    for attack_id in ("loop-identical-001", "loop-paging-001"):
        assert outcomes[("none", attack_id)].success is True
        assert outcomes[("loop_detect", attack_id)].success is False


def test_resume_skips_completed_episodes(tmp_path: Path) -> None:
    config = _config()
    first = _execute(config, tmp_path)
    second = _execute(config, tmp_path)
    lines = (tmp_path / "pilot-mock" / "episodes.jsonl").read_text().splitlines()
    assert len(lines) == 40
    assert first == second


def test_existing_run_without_resume_is_refused(tmp_path: Path) -> None:
    _execute(_config(), tmp_path)
    with pytest.raises(RunExistsError):
        _execute(_config(), tmp_path, resume=False)
