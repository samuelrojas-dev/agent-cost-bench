"""Versioned price table: every entry carries its source and retrieval date."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dowbench.metering.usage import Usage

DEFAULT_PRICES = Path(__file__).with_name("pricing.yaml")

_PER_MTOK = 1_000_000


class PricingError(KeyError):
    pass


class ModelPrice(BaseModel):
    """USD per million tokens for one model."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cache_read_per_mtok: float = Field(ge=0)
    cache_write_per_mtok: float = Field(ge=0)
    source: str = Field(min_length=1)
    retrieved: dt.date
    simulated: bool = False

    def cost_usd(self, usage: Usage) -> float:
        return (
            usage.input_tokens * self.input_per_mtok
            + (usage.output_tokens + usage.reasoning_tokens) * self.output_per_mtok
            + usage.cache_read_tokens * self.cache_read_per_mtok
            + usage.cache_write_tokens * self.cache_write_per_mtok
        ) / _PER_MTOK

    @property
    def max_usd_per_token(self) -> float:
        return (
            max(
                self.input_per_mtok,
                self.output_per_mtok,
                self.cache_read_per_mtok,
                self.cache_write_per_mtok,
            )
            / _PER_MTOK
        )


class _PriceFile(BaseModel):
    prices: list[ModelPrice]


class PriceTable:
    def __init__(self, prices: list[ModelPrice]) -> None:
        self._prices: dict[tuple[str, str], ModelPrice] = {}
        for price in prices:
            key = (price.provider, price.model)
            if key in self._prices:
                raise ValueError(f"duplicate price entry for {key}")
            self._prices[key] = price

    @classmethod
    def load(cls, path: Path = DEFAULT_PRICES) -> PriceTable:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(_PriceFile.model_validate(data).prices)

    def get(self, provider: str, model: str) -> ModelPrice:
        try:
            return self._prices[(provider, model)]
        except KeyError:
            raise PricingError(f"no price for {provider}/{model}; add it to pricing.yaml") from None
