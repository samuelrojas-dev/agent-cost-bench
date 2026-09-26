import pytest

from dowbench.runner.budget import BudgetError, Spend, check_budget


def _check(**overrides: object) -> None:
    kwargs: dict[str, object] = {
        "worst_tokens": 1000,
        "worst_usd": 1.0,
        "already": Spend(tokens=0, usd=0.0),
        "budget_usd": 2.0,
        "budget_tokens": None,
        "simulated": False,
    }
    kwargs.update(overrides)
    check_budget(**kwargs)  # type: ignore[arg-type]


def test_spent_plus_worst_case_must_fit_the_usd_budget() -> None:
    _check(already=Spend(tokens=500, usd=1.0))  # 1.0 + 1.0 <= 2.0
    with pytest.raises(BudgetError, match=r"spent \$1\.0001 \+ worst case \$1\.0000"):
        _check(already=Spend(tokens=500, usd=1.0001))


def test_spent_plus_worst_case_must_fit_the_token_budget() -> None:
    _check(budget_tokens=1500, already=Spend(tokens=500, usd=0.0))
    with pytest.raises(BudgetError, match="1,501 tokens exceeds budget 1,500"):
        _check(budget_tokens=1500, already=Spend(tokens=501, usd=0.0))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"budget_usd": None}, "real providers require --budget-usd"),
        ({"worst_usd": None, "budget_usd": None}, "require --budget-tokens"),
        ({"worst_usd": None, "budget_tokens": 10_000}, "cannot be checked in USD"),
        ({"budget_usd": float("nan")}, "exceeds budget"),
    ],
)
def test_missing_or_unusable_budgets_are_refused(
    overrides: dict[str, object], message: str
) -> None:
    with pytest.raises(BudgetError, match=message):
        _check(**overrides)
