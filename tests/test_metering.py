import datetime as dt

import pytest
from pydantic import ValidationError

from dowbench.metering.pricing import ModelPrice, PriceTable, PricingError
from dowbench.metering.usage import Usage


def _price(**overrides: object) -> ModelPrice:
    fields: dict[str, object] = {
        "provider": "p",
        "model": "m",
        "input_per_mtok": 1.0,
        "output_per_mtok": 5.0,
        "cache_read_per_mtok": 0.1,
        "cache_write_per_mtok": 1.25,
        "source": "test",
        "retrieved": dt.date(2026, 9, 25),
        "max_prompt_tokens": 200_000,
    }
    fields.update(overrides)
    return ModelPrice.model_validate(fields)


def test_usage_addition_and_total() -> None:
    a = Usage(input_tokens=10, output_tokens=5, reasoning_tokens=2)
    b = Usage(input_tokens=1, cache_read_tokens=3, cache_write_tokens=4)
    total = a + b
    assert total == Usage(
        input_tokens=11,
        output_tokens=5,
        reasoning_tokens=2,
        cache_read_tokens=3,
        cache_write_tokens=4,
    )
    assert total.total_tokens == 25


def test_usage_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1)


def test_cost_prices_reasoning_as_output() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        reasoning_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert _price().cost_usd(usage) == pytest.approx(1.0 + 5.0 + 5.0 + 0.1 + 1.25)


def test_max_usd_per_token() -> None:
    assert _price().max_usd_per_token == pytest.approx(5.0 / 1_000_000)


def test_default_table_has_only_simulated_mock_entry() -> None:
    table = PriceTable.load()
    assert table.get("mock", "mock-1").simulated
    with pytest.raises(PricingError):
        table.get("anthropic", "unknown-model")


def test_duplicate_entries_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        PriceTable([_price(), _price()])


def test_real_prices_must_declare_their_tier() -> None:
    with pytest.raises(ValidationError, match="max_prompt_tokens"):
        _price(max_prompt_tokens=None)
    assert _price(max_prompt_tokens=None, simulated=True).max_prompt_tokens is None


def test_prompt_limit_within_tier_passes_and_beyond_fails() -> None:
    price = _price(max_prompt_tokens=200_000)
    price.check_prompt_limit(200_000)
    with pytest.raises(PricingError, match="exceed the priced tier"):
        price.check_prompt_limit(200_001)
