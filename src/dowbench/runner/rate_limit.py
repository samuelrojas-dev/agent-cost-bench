"""Provider rate limiting for real runs (ADR 0014).

Free tiers cap requests per minute (RPM), tokens per minute (TPM) and requests per day
(RPD). Until it is verified that ``countTokens`` is free of the request quota, every
``countTokens`` counts as a request, next to the ``generate`` call it precedes (ADR 0003).
The limiter paces RPM/TPM at call time and the runner checks RPD between episodes; it never
makes a call itself, and the clock and sleep are injected so tests are deterministic.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from dowbench.providers.base import Provider, Request, Response


class RateLimit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rpm: int = Field(gt=0, description="requests per minute")
    tpm: int = Field(gt=0, description="tokens per minute")
    rpd: int = Field(gt=0, description="requests per day")


class RatePlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    requests: int
    count_requests: int  # countTokens calls, each a request while unverified (ADR 0014)
    generate_requests: int
    tokens: int
    minutes: float
    days: int


def plan(limit: RateLimit, *, generate_calls: int, tokens: int) -> RatePlan:
    """Worst-case wall time and days for ``generate_calls`` under ``limit``.

    Each generate call is preceded by one ``countTokens`` (ADR 0003); both count as requests.
    """
    count = generate_calls
    requests = generate_calls + count
    minutes = max(requests / limit.rpm, tokens / limit.tpm) if requests else 0.0
    days = math.ceil(requests / limit.rpd) if requests else 0
    return RatePlan(
        requests=requests,
        count_requests=count,
        generate_requests=generate_calls,
        tokens=tokens,
        minutes=minutes,
        days=days,
    )


class RateLimitReached(RuntimeError):
    """The daily request budget (RPD) is exhausted; the run stops between episodes."""


_MINUTE = 60.0


class RateLimiter:
    """Sliding-window RPM/TPM pacing plus a hard RPD cap (ADR 0014)."""

    def __init__(
        self,
        limit: RateLimit,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._limit = limit
        self._now = now
        self._sleep = sleep
        self._window: deque[tuple[float, int]] = deque()  # (timestamp, tokens) last minute
        self._day_requests = 0

    @property
    def day_requests(self) -> int:
        return self._day_requests

    def reserve_episode(self, worst_case_requests: int) -> None:
        """Refuse before an episode whose worst case would cross the daily cap (ADR 0014)."""
        if self._day_requests + worst_case_requests > self._limit.rpd:
            raise RateLimitReached(
                f"daily request budget reached: {self._day_requests} used, "
                f"{worst_case_requests} more would exceed RPD {self._limit.rpd}. "
                "Resume tomorrow; recorded episodes are skipped."
            )

    def acquire(self, tokens: int) -> None:
        """Block until one request of ``tokens`` fits the RPM and TPM windows, then record it."""
        while True:
            now = self._now()
            cutoff = now - _MINUTE
            while self._window and self._window[0][0] <= cutoff:
                self._window.popleft()
            # An empty window always admits: a single call cannot be paced below its own
            # size, so a call larger than RPM/TPM proceeds instead of blocking forever.
            if not self._window:
                self._window.append((now, tokens))
                self._day_requests += 1
                return
            requests_in_window = len(self._window)
            tokens_in_window = sum(t for _, t in self._window)
            rpm_ok = requests_in_window < self._limit.rpm
            tpm_ok = tokens_in_window + tokens <= self._limit.tpm
            if rpm_ok and tpm_ok:
                self._window.append((now, tokens))
                self._day_requests += 1
                return
            # Wait until the oldest event leaves the one-minute window, then re-check.
            self._sleep(max(self._window[0][0] + _MINUTE - now, 0.0))


class RateLimitedProvider:
    """Wraps a provider so ``count_tokens`` and ``complete`` pass through the limiter."""

    def __init__(self, inner: Provider, limiter: RateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def simulated(self) -> bool:
        return self._inner.simulated

    def begin_episode(self, episode_id: str, attempt: str) -> None:
        # Forward the per-episode hook so a wrapped recorder or replayer still receives it
        # (ADR 0015); the runner calls it on the outermost provider only.
        begin = getattr(self._inner, "begin_episode", None)
        if begin is not None:
            begin(episode_id, attempt)

    def count_tokens(self, request: Request) -> int | None:
        self._limiter.acquire(0)  # a countTokens call: a request, no billed tokens
        return self._inner.count_tokens(request)

    def complete(self, request: Request) -> Response:
        self._limiter.acquire(request.max_tokens)  # reserve the call's token ceiling
        return self._inner.complete(request)
