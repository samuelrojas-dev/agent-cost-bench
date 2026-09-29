"""Transient-error classification and the retry decorator (ADR 0020)."""

from __future__ import annotations

import pytest

from dowbench.metering.usage import Usage
from dowbench.providers.base import (
    Request,
    Response,
    TransientProviderError,
    UsageMappingError,
    is_transient_error,
    transient_boundary,
)
from dowbench.providers.retry import RetryingProvider, RetryPolicy


class _Status(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _Code(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"code {code}")
        self.code = code


class ConnectionResetError2(Exception):  # name matched by the timeout/connection regex
    pass


def test_is_transient_error_by_status_code() -> None:
    assert is_transient_error(_Status(429))
    assert is_transient_error(_Status(503))
    assert is_transient_error(_Code(500))  # Gemini SDK uses `code`
    assert not is_transient_error(_Status(400))  # a bad request is terminal
    assert not is_transient_error(_Status(401))


def test_is_transient_error_by_name() -> None:
    assert is_transient_error(TimeoutError("read timed out"))
    assert is_transient_error(ConnectionResetError2())
    assert not is_transient_error(ValueError("bad"))
    assert not is_transient_error(UsageMappingError("unpriceable"))


def test_transient_boundary_wraps_only_transient() -> None:
    with pytest.raises(TransientProviderError), transient_boundary():
        raise _Status(429)
    # terminal errors pass through unchanged
    with pytest.raises(UsageMappingError), transient_boundary():
        raise UsageMappingError("unpriceable")


class _Flaky:
    """A provider whose complete() fails transiently a fixed number of times, then succeeds."""

    name = "flaky"
    simulated = False

    def __init__(self, fails: int) -> None:
        self.fails = fails
        self.attempts = 0

    def _ok(self) -> Response:
        return Response(stop_reason="end_turn", usage=Usage(input_tokens=1, output_tokens=1))

    def complete(self, request: Request) -> Response:
        self.attempts += 1
        if self.attempts <= self.fails:
            raise _Status(429)
        return self._ok()

    def count_tokens(self, request: Request) -> int | None:
        return 1


def _req() -> Request:
    return Request(model="m", system="", messages=[], max_tokens=8)


def test_retry_recovers_within_budget() -> None:
    inner = _Flaky(fails=2)
    slept: list[float] = []
    provider = RetryingProvider(
        inner, RetryPolicy(max_attempts=3, base_delay_s=1.0), sleep=slept.append
    )
    provider.complete(_req())  # succeeds on the third attempt
    assert inner.attempts == 3
    assert slept == [1.0, 2.0]  # exponential backoff between the two retries


def test_retry_exhausted_raises_transient() -> None:
    inner = _Flaky(fails=5)
    provider = RetryingProvider(
        inner, RetryPolicy(max_attempts=2, base_delay_s=0), sleep=lambda _: None
    )
    with pytest.raises(TransientProviderError, match="after 2 attempts"):
        provider.complete(_req())
    assert inner.attempts == 2


def test_retry_does_not_swallow_terminal_errors() -> None:
    class _Terminal:
        name = "t"
        simulated = False

        def complete(self, request: Request) -> Response:
            raise UsageMappingError("unpriceable")

        def count_tokens(self, request: Request) -> int | None:
            return 1

    provider = RetryingProvider(_Terminal(), RetryPolicy(base_delay_s=0), sleep=lambda _: None)
    with pytest.raises(UsageMappingError):
        provider.complete(_req())
