"""Bounded, visible retry of transient provider failures (ADR 0020, #37).

A rate limit (429) or a 5xx is a *rejected* request, not a billed one, so retrying it adds no
spend (ADR 0010). This decorator retries such errors with exponential backoff and, once the
attempts are spent, re-raises the ``TransientProviderError`` so the run can leave the episode
unfinished for ``--resume`` rather than skip it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from dowbench.providers.base import (
    Provider,
    Request,
    Response,
    TransientProviderError,
    is_transient_error,
)

_T = TypeVar("_T")


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to retry a transient failure, and the backoff between tries."""

    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise ValueError("backoff delays must be >= 0")

    def backoff_s(self, attempt: int) -> float:
        """Delay before ``attempt`` (1-based): base · 2^(attempt-1), capped."""
        return float(min(self.base_delay_s * (2 ** (attempt - 1)), self.max_delay_s))


class RetryingProvider:
    """Wrap a provider so transient failures of ``complete``/``count_tokens`` are retried."""

    def __init__(
        self,
        inner: Provider,
        policy: RetryPolicy,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._policy = policy
        self._sleep = sleep

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def simulated(self) -> bool:
        return self._inner.simulated

    @property
    def counts_tokens(self) -> bool:
        return self._inner.counts_tokens

    def begin_episode(self, episode_id: str, attempt: str) -> None:
        begin = getattr(self._inner, "begin_episode", None)
        if begin is not None:
            begin(episode_id, attempt)

    def count_tokens(self, request: Request) -> int | None:
        return self._retry(lambda: self._inner.count_tokens(request))

    def complete(self, request: Request) -> Response:
        return self._retry(lambda: self._inner.complete(request))

    def _retry(self, call: Callable[[], _T]) -> _T:
        last: BaseException | None = None
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                return call()
            except BaseException as exc:
                if not is_transient_error(exc):
                    raise
                last = exc
                if attempt < self._policy.max_attempts:
                    self._sleep(self._policy.backoff_s(attempt))
        raise TransientProviderError(
            f"transient provider error after {self._policy.max_attempts} attempts: {last}"
        ) from last
