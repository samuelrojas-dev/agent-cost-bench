"""Cumulative budget of a run directory (ADR 0010).

`--budget-usd` and `--budget-tokens` cap everything a run directory spends across all
invocations: what `calls.jsonl` already records (completed, errored and interrupted
attempts alike) plus the worst case of the episodes still pending.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from dowbench.metering.pricing import PriceTable
from dowbench.metering.usage import Usage
from dowbench.runner.config import RunConfig
from dowbench.runner.store import RunStore


class BudgetError(RuntimeError):
    pass


class Spend(BaseModel):
    tokens: int
    usd: float | None  # None for unpriced runs (ADR 0008)


def spent(store: RunStore, config: RunConfig, prices: PriceTable) -> Spend:
    """Everything already billed in this run directory, from calls.jsonl."""
    tokens, usd = 0, 0.0
    if store.calls_path.exists():
        with store.calls_path.open(encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                usage = Usage.model_validate(row["usage"])
                tokens += usage.total_tokens
                if not config.unpriced:
                    usd += prices.get(config.provider, row["model"]).cost_usd(usage)
    return Spend(tokens=tokens, usd=None if config.unpriced else usd)


def check_budget(
    *,
    worst_tokens: int,
    worst_usd: float | None,
    already: Spend,
    budget_usd: float | None,
    budget_tokens: int | None,
    simulated: bool,
) -> None:
    """Refuse unless already spent plus the pending worst case fits every given budget."""
    if worst_usd is None:
        if budget_usd is not None:
            raise BudgetError("unpriced runs cannot be checked in USD; use --budget-tokens")
        if not simulated and budget_tokens is None:
            raise BudgetError("unpriced real runs require --budget-tokens")
    elif not simulated and budget_usd is None:
        raise BudgetError("real providers require --budget-usd")
    elif budget_usd is not None:
        total_usd = (already.usd or 0.0) + worst_usd
        if not total_usd <= budget_usd:
            raise BudgetError(
                f"spent ${already.usd or 0.0:.4f} + worst case ${worst_usd:.4f} = "
                f"${total_usd:.4f} exceeds budget ${budget_usd:.4f}; lower the ceiling, "
                "the matrix, or raise the budget"
            )
    if budget_tokens is not None:
        total_tokens = already.tokens + worst_tokens
        if total_tokens > budget_tokens:
            raise BudgetError(
                f"spent {already.tokens:,} + worst case {worst_tokens:,} = {total_tokens:,} "
                f"tokens exceeds budget {budget_tokens:,}"
            )
