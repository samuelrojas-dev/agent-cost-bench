"""Versioned price table: every entry carries its source and retrieval date."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # Largest prompt these rates apply to: the upper edge of the cheapest tier, or the
    # context window when pricing is flat. Required for real models (ADR 0005).
    max_prompt_tokens: int | None = Field(default=None, gt=0)
    simulated: bool = False

    @model_validator(mode="after")
    def _real_prices_are_sourced(self) -> ModelPrice:
        if self.simulated:
            return self
        if not self.source.startswith("https://"):
            raise ValueError(f"{self.provider}/{self.model}: real prices need an https source URL")
        if self.max_prompt_tokens is None:
            raise ValueError(
                f"{self.provider}/{self.model}: real prices need max_prompt_tokens (ADR 0005)"
            )
        return self

    def check_prompt_limit(self, max_prompt_tokens: int) -> None:
        """Refuse a run whose prompts could leave the tier these rates describe."""
        if self.max_prompt_tokens is not None and max_prompt_tokens > self.max_prompt_tokens:
            raise PricingError(
                f"{self.provider}/{self.model}: prompts up to {max_prompt_tokens:,} tokens "
                f"exceed the priced tier of {self.max_prompt_tokens:,}; lower "
                "ceiling.max_total_tokens"
            )

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
