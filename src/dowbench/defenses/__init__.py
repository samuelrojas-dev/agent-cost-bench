"""Defense registry and the config-facing ``DefenseSpec``."""

from __future__ import annotations

from functools import cache
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dowbench.defenses.base import Abort, Defense
from dowbench.registry import load_all

NO_DEFENSE = "none"
DEFENSE_GROUP = "dowbench.defenses"


@cache
def defense_registry() -> dict[str, type[Defense]]:
    """The installed defenses, name -> class, from the ``dowbench.defenses`` entry points."""
    return {name: cast(type[Defense], obj) for name, obj in load_all(DEFENSE_GROUP).items()}


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
    registry = defense_registry()
    try:
        cls = registry[spec.name]
    except KeyError:
        known = ", ".join(sorted([NO_DEFENSE, *registry]))
        raise ValueError(f"unknown defense {spec.name!r}; known: {known}") from None
    try:
        return [cls(**spec.params)]
    except TypeError as exc:
        raise ValueError(f"defense {spec.name!r}: {exc}") from None


__all__ = [
    "NO_DEFENSE",
    "Abort",
    "Defense",
    "DefenseSpec",
    "build_defenses",
    "defense_registry",
]
