"""Rate limiter tests (ADR 0014). Offline, with an injected clock: no real time, no calls."""

from __future__ import annotations

from pathlib import Path

import pytest

from dowbench.metering.usage import Usage
from dowbench.providers.base import Message, Request, Response
from dowbench.runner.rate_limit import (
    RateLimit,
    RateLimitedProvider,
    RateLimiter,
    RateLimitReached,
    plan,
)


class FakeClock:
    """A monotonic clock that only advances when the limiter sleeps."""

    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _limiter(rpm: int = 12, tpm: int = 250_000, rpd: int = 500) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    limit = RateLimit(rpm=rpm, tpm=tpm, rpd=rpd)
    return RateLimiter(limit, now=clock.now, sleep=clock.sleep), clock


# --- plan (what estimate reports) ---


def test_plan_matches_the_gemini_pilot() -> None:
    p = plan(RateLimit(rpm=12, tpm=250_000, rpd=500), generate_calls=384, tokens=1_600_000)
    assert (p.generate_requests, p.count_requests, p.requests) == (384, 384, 768)
    assert p.minutes == pytest.approx(64.0)  # 768 / 12 dominates 1.6M / 250k = 6.4
    assert p.days == 2  # ceil(768 / 500)


def test_plan_is_zero_for_no_calls() -> None:
    p = plan(RateLimit(rpm=12, tpm=250_000, rpd=500), generate_calls=0, tokens=0)
    assert (p.requests, p.minutes, p.days) == (0, 0.0, 0)


# --- RPM / TPM pacing ---


def test_rpm_paces_after_the_limit_is_hit() -> None:
    limiter, clock = _limiter(rpm=2, tpm=10**9, rpd=10**9)
    limiter.acquire(0)
    limiter.acquire(0)
    assert clock.slept == []  # first two requests fit the minute
    limiter.acquire(0)  # third must wait for the first to leave the window
    assert clock.slept and clock.t == pytest.approx(60.0)
    assert limiter.day_requests == 3


def test_tpm_paces_on_tokens() -> None:
    limiter, clock = _limiter(rpm=10**9, tpm=100, rpd=10**9)
    limiter.acquire(60)
    limiter.acquire(40)  # exactly fills the token window, no wait
    assert clock.slept == []
    limiter.acquire(60)  # 60 more would exceed 100 within the minute -> wait
    assert clock.slept and clock.t == pytest.approx(60.0)


# --- RPD hard cap between episodes ---


def test_reserve_episode_stops_when_the_day_budget_would_be_exceeded() -> None:
    limiter, _ = _limiter(rpd=10)
    for _ in range(8):
        limiter.acquire(0)
    assert limiter.day_requests == 8
    limiter.reserve_episode(2)  # 8 + 2 == 10, fits
    with pytest.raises(RateLimitReached, match="RPD 10"):
        limiter.reserve_episode(3)  # 8 + 3 > 10


# --- provider wrapper counts both call kinds ---


class _FakeProvider:
    name = "gemini"
    simulated = False

    def __init__(self) -> None:
        self.acquired: list[int] = []

    def count_tokens(self, request: Request) -> int:
        return 5

    def complete(self, request: Request) -> Response:
        return Response(stop_reason="end_turn", usage=Usage(input_tokens=5))


def _request() -> Request:
    return Request(
        model="m", system="s", messages=[Message(role="user", content="hi")], max_tokens=128
    )


def test_wrapper_counts_counttokens_and_generate_as_requests() -> None:
    limiter, _ = _limiter(rpm=10**9, tpm=10**9, rpd=10**9)
    wrapped = RateLimitedProvider(_FakeProvider(), limiter)
    wrapped.count_tokens(_request())
    wrapped.complete(_request())
    assert limiter.day_requests == 2  # both the count and the generate counted
    assert wrapped.name == "gemini" and wrapped.simulated is False


def test_wrapper_reserves_the_call_token_ceiling_for_tpm() -> None:
    # tpm just below one call's max_tokens: the generate call must wait, the count must not.
    limiter, clock = _limiter(rpm=10**9, tpm=127, rpd=10**9)
    wrapped = RateLimitedProvider(_FakeProvider(), limiter)
    wrapped.count_tokens(_request())  # 0 tokens, no wait
    assert clock.slept == []
    # reserves 128 > 127: waits for the countTokens event to leave, then the empty window admits
    wrapped.complete(_request())
    assert clock.slept


# --- config wiring ---


def test_pilot_config_exposes_the_rate_plan_in_estimate() -> None:
    from dowbench.attacks.schema import load_dataset
    from dowbench.metering.pricing import PriceTable
    from dowbench.runner.config import RunConfig
    from dowbench.runner.execute import estimate
    from dowbench.runner.matrix import plan_episodes

    config = RunConfig.from_yaml(Path(__file__).parent.parent / "configs" / "pilot-gemini.yaml")
    assert config.rate_limit == RateLimit(rpm=12, tpm=250_000, rpd=500)
    specs = plan_episodes(config, load_dataset())
    result = estimate(config, specs, PriceTable.load(), done=set())
    assert result.rate_plan is not None
    assert result.rate_plan.requests == 768
    assert result.rate_plan.days == 2


# --- no key ever reaches an output file or the CLI logs ---


def test_api_keys_never_appear_in_outputs_or_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from dowbench.cli import app

    # Realistic key-shaped sentinels set in the environment before running.
    gemini_key = "AIza" + "K" * 35
    anthropic_key = "sk-ant-" + "S" * 40
    monkeypatch.setenv("GEMINI_API_KEY", gemini_key)
    monkeypatch.setenv("ANTHROPIC_API_KEY", anthropic_key)

    pilot = str(Path(__file__).parent.parent / "configs" / "pilot.yaml")  # mock, no real calls
    result = CliRunner().invoke(app, ["run", pilot, "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output

    run_dir = tmp_path / "pilot-mock"
    haystacks = [result.output]  # the CLI logs (stdout+stderr)
    haystacks += [p.read_text(encoding="utf-8") for p in run_dir.glob("*.jsonl")]
    haystacks += [p.read_text(encoding="utf-8") for p in run_dir.glob("*.json")]
    assert any("calls.jsonl" in p.name for p in run_dir.iterdir())  # outputs were written

    for text in haystacks:
        assert gemini_key not in text
        assert anthropic_key not in text
        assert "AIza" not in text
        assert "sk-ant-" not in text
