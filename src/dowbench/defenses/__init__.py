"""Defense registry and the config-facing ``DefenseSpec``."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dowbench.defenses.base import Abort, Defense
from dowbench.defenses.limits import LoopDetect, TokenBudget, TurnLimit

NO_DEFENSE = "none"

REGISTRY: dict[str, type[Defense]] = {cls.name: cls for cls in (TokenBudget, TurnLimit, LoopDetect)}


class DefenseSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    label: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.label or self.name

    @model_validator(mode="after")
    def _buildable(self) -> DefenseSpec:
        build_defenses(self)
        return self


def build_defenses(spec: DefenseSpec) -> list[Defense]:
    if spec.name == NO_DEFENSE:
        if spec.params:
            raise ValueError("defense 'none' takes no params")
        return []
    try:
        cls = REGISTRY[spec.name]
    except KeyError:
        known = ", ".join(sorted([NO_DEFENSE, *REGISTRY]))
        raise ValueError(f"unknown defense {spec.name!r}; known: {known}") from None
    try:
        return [cls(**spec.params)]
    except TypeError as exc:
        raise ValueError(f"defense {spec.name!r}: {exc}") from None


__all__ = ["NO_DEFENSE", "REGISTRY", "Abort", "Defense", "DefenseSpec", "build_defenses"]
