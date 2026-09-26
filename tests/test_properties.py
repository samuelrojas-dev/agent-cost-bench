"""Property-based checks of the invariants every cost number depends on."""

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dowbench.metering.pricing import ModelPrice
from dowbench.metering.usage import Usage
from dowbench.metrics import wilson_interval

counts = st.integers(min_value=0, max_value=10**9)
usages = st.builds(
    Usage,
    input_tokens=counts,
    output_tokens=counts,
    reasoning_tokens=counts,
    cache_read_tokens=counts,
    cache_write_tokens=counts,
)
rates = st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False)
prices = st.builds(
    ModelPrice,
    provider=st.just("p"),
    model=st.just("m"),
    input_per_mtok=rates,
    output_per_mtok=rates,
    cache_read_per_mtok=rates,
    cache_write_per_mtok=rates,
    source=st.just("test"),
    retrieved=st.just(dt.date(2026, 9, 25)),
)


@given(usages, usages, usages)
def test_usage_addition_is_associative_and_commutative(a: Usage, b: Usage, c: Usage) -> None:
    assert (a + b) + c == a + (b + c)
    assert a + b == b + a
    assert a + Usage() == a


@given(usages, usages)
def test_total_tokens_is_additive(a: Usage, b: Usage) -> None:
    assert (a + b).total_tokens == a.total_tokens + b.total_tokens


@given(prices, usages, usages)
def test_cost_is_additive_and_non_negative(price: ModelPrice, a: Usage, b: Usage) -> None:
    assert price.cost_usd(a) >= 0
    assert price.cost_usd(a + b) == pytest.approx(
        price.cost_usd(a) + price.cost_usd(b), rel=1e-9, abs=1e-9
    )


@given(prices, usages)
def test_cost_is_bounded_by_max_rate_times_tokens(price: ModelPrice, usage: Usage) -> None:
    bound = price.max_usd_per_token * usage.total_tokens
    assert price.cost_usd(usage) <= bound * (1 + 1e-9) + 1e-12


@given(
    st.integers(min_value=1, max_value=10_000).flatmap(
        lambda n: st.tuples(st.integers(min_value=0, max_value=n), st.just(n))
    )
)
def test_wilson_interval_contains_point_estimate(pair: tuple[int, int]) -> None:
    successes, n = pair
    low, high = wilson_interval(successes, n)
    assert 0.0 <= low <= successes / n <= high <= 1.0
