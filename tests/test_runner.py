import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dowbench.attacks.schema import load_dataset
from dowbench.metering.pricing import PriceTable, PricingError
from dowbench.providers.base import Request, Response, UsageMappingError
from dowbench.providers.mock import MockProvider
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import (
    EpisodeErroredError,
    RunExistsError,
    build_provider,
    estimate,
    execute,
)
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
        _config(provider="nope")


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


def test_ceiling_beyond_priced_tier_refuses_estimate_and_execute(tmp_path: Path) -> None:
    mock = PriceTable.load().get("mock", "mock-1")
    prices = PriceTable([mock.model_copy(update={"max_prompt_tokens": 10_000})])
    config = _config()
    dataset = load_dataset()
    specs = plan_episodes(config, dataset)
    with pytest.raises(PricingError, match="priced tier"):
        estimate(config, specs, prices, done=set())
    with pytest.raises(PricingError, match="priced tier"):
        execute(
            config,
            dataset,
            provider=build_provider(config, dataset),
            prices=prices,
            out_dir=tmp_path,
        )
    assert not (tmp_path / config.run_name / "episodes.jsonl").exists()


def test_pilot_run_writes_calls_episodes_and_summary(tmp_path: Path) -> None:
    summary = _execute(_config(), tmp_path)
    run_dir = tmp_path / "pilot-mock"

    episodes = [json.loads(line) for line in (run_dir / "episodes.jsonl").read_text().splitlines()]
    calls = [json.loads(line) for line in (run_dir / "calls.jsonl").read_text().splitlines()]
    assert len(episodes) == 40
    assert len(calls) == sum(e["turns"] for e in episodes)
    assert {c["episode_id"] for c in calls} <= {e["episode_id"] for e in episodes}
    # The provider's raw usage is persisted per call and matches the normalized usage.
    assert all(c["raw_usage"] == c["usage"] for c in calls)

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


@pytest.mark.parametrize("path", sorted(PILOT.parent.glob("*.yaml")), ids=lambda p: p.name)
def test_every_shipped_config_is_valid_and_bounded(path: Path) -> None:
    config = RunConfig.from_yaml(path)
    specs = plan_episodes(config, load_dataset())
    result = estimate(config, specs, PriceTable.load(), done=set())
    assert result.worst_case_tokens > 0
    if config.unpriced:
        assert result.worst_case_usd is None
    else:
        assert result.worst_case_usd is not None and result.worst_case_usd > 0


def test_benign_tasks_alone_plan_one_episode_per_model_and_defense() -> None:
    dataset = load_dataset()
    task = dataset.benign[0].id
    config = _config(attacks=[], benign_tasks=[task], defenses=[{"name": "none"}])
    specs = plan_episodes(config, dataset)
    assert [(s.kind, s.item_id) for s in specs] == [("benign", task)]
    with pytest.raises(ValueError, match="unknown benign task"):
        plan_episodes(_config(benign_tasks=["nope"]), dataset)


def test_unpriced_run_counts_tokens_and_never_invents_usd(tmp_path: Path) -> None:
    config = _config(unpriced=True)
    dataset = load_dataset()
    specs = plan_episodes(config, dataset)
    empty = PriceTable([])  # no price for anything: must not be needed
    result = estimate(config, specs, empty, done=set())
    assert result.worst_case_usd is None
    assert result.max_model_calls == config.ceiling.max_turns * len(specs)
    summary = execute(
        config, dataset, provider=build_provider(config, dataset), prices=empty, out_dir=tmp_path
    )
    episodes = _rows(tmp_path / config.run_name / "episodes.jsonl")
    assert episodes and all(e["cost_usd"] is None for e in episodes)
    assert all(e["usage"]["input_tokens"] > 0 for e in episodes)
    assert all(o.amplification is None for o in summary.attacks)


class _FailingProvider(MockProvider):
    """Real-looking provider: the n-th call of the whole run fails with ``error``."""

    simulated = False

    def __init__(self, fail_on: int, error: Exception) -> None:
        super().__init__()
        self.calls = 0
        self._fail_on = fail_on
        self._error = error

    def complete(self, request: Request) -> Response:
        self.calls += 1
        if self.calls == self._fail_on:
            raise self._error
        response = super().complete(request)
        return response.model_copy(update={"raw": {"usage": response.usage.model_dump()}})


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_unpriceable_call_is_recorded_stops_the_run_and_is_not_retried(tmp_path: Path) -> None:
    config, dataset = _config(), load_dataset()
    error = UsageMappingError("service tier 'priority' is not priced", {"service_tier": "p"})
    provider = _FailingProvider(fail_on=3, error=error)
    with pytest.raises(EpisodeErroredError, match="priority"):
        execute(config, dataset, provider=provider, prices=PriceTable.load(), out_dir=tmp_path)
    run_dir = tmp_path / config.run_name
    calls, episodes = _rows(run_dir / "calls.jsonl"), _rows(run_dir / "episodes.jsonl")
    assert len(calls) == 3  # every billed call is on disk, the failed one included
    assert calls[-1]["raw_usage"] == {"service_tier": "p"}
    assert calls[-1]["error"]
    assert [e["status"] for e in episodes] == ["completed", "errored"]
    assert {c["attempt"] for c in calls} == {e["attempt"] for e in episodes}

    # Resuming continues after the errored episode instead of paying for it again.
    resumed = _FailingProvider(fail_on=0, error=error)
    execute(config, dataset, provider=resumed, prices=PriceTable.load(), out_dir=tmp_path)
    episodes = _rows(run_dir / "episodes.jsonl")
    assert sum(e["episode_id"] == episodes[1]["episode_id"] for e in episodes) == 1


def test_interrupted_episode_keeps_its_billed_calls(tmp_path: Path) -> None:
    config, dataset = _config(), load_dataset()
    provider = _FailingProvider(fail_on=2, error=TimeoutError("read timed out"))
    with pytest.raises(TimeoutError):
        execute(config, dataset, provider=provider, prices=PriceTable.load(), out_dir=tmp_path)
    run_dir = tmp_path / config.run_name
    calls = _rows(run_dir / "calls.jsonl")
    assert len(calls) == 1  # the call billed before the timeout survives
    assert _rows(run_dir / "episodes.jsonl") == []  # the episode is incomplete, so resumable
    orphan_attempt = calls[0]["attempt"]

    resumed = _FailingProvider(fail_on=0, error=TimeoutError())
    execute(config, dataset, provider=resumed, prices=PriceTable.load(), out_dir=tmp_path)
    episodes = _rows(run_dir / "episodes.jsonl")
    assert orphan_attempt not in {e["attempt"] for e in episodes}


def test_calls_record_model_version_and_requests_are_kept_sanitized(tmp_path: Path) -> None:
    config = _config()
    _execute(config, tmp_path)
    run_dir = tmp_path / config.run_name
    calls, requests = _rows(run_dir / "calls.jsonl"), _rows(run_dir / "requests.jsonl")
    assert {c["model_version"] for c in calls} == {"mock-1-simulated"}
    assert "request_body" not in calls[0]
    assert len(requests) == len(calls)
    assert [(r["episode_id"], r["turn"]) for r in requests] == [
        (c["episode_id"], c["turn"]) for c in calls
    ]
    assert requests[0]["request"] == {"model": "mock-1", "messages": 1}
    info = json.loads((run_dir / "run.json").read_text())
    assert info["served_model_versions"] == {"mock-1": ["mock-1-simulated"]}


def test_run_json_names_served_versions_even_when_the_run_stops(tmp_path: Path) -> None:
    config, dataset = _config(), load_dataset()
    provider = _FailingProvider(fail_on=2, error=TimeoutError("read timed out"))
    with pytest.raises(TimeoutError):
        execute(config, dataset, provider=provider, prices=PriceTable.load(), out_dir=tmp_path)
    info = json.loads((tmp_path / config.run_name / "run.json").read_text())
    assert info["served_model_versions"] == {"mock-1": ["mock-1-simulated"]}
