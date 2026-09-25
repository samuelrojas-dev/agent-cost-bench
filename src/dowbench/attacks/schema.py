"""Attack dataset: benign tasks and the attacks paired with them."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dowbench.agent.tools import TOOL_NAMES

DATA_DIR = Path(__file__).with_name("data")

Family = Literal["tool_loop", "output_flood", "reasoning_bomb", "context_bloat"]
Vector = Literal["user_prompt", "tool_result"]
# What a susceptible agent does when the attack works. The mock provider imitates it.
Signal = Literal[
    "repeated_identical_calls",
    "repeated_tool_calls",
    "long_output",
    "long_reasoning",
    "context_growth",
]


class BenignTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    tool: str

    @model_validator(mode="after")
    def _known_tool(self) -> BenignTask:
        if self.tool not in TOOL_NAMES:
            raise ValueError(f"benign task {self.id}: unknown tool {self.tool!r}")
        return self


class Attack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    family: Family
    vector: Vector
    benign_task: str
    payload: str = Field(min_length=1)
    payload_repeat: int = Field(default=1, ge=1, le=1000)
    target_tool: str | None = None
    expected_signal: Signal
    source: str = Field(min_length=1, description="Where the attack pattern comes from")

    @model_validator(mode="after")
    def _target_tool_matches_vector(self) -> Attack:
        if self.vector == "tool_result" and self.target_tool is None:
            raise ValueError(f"attack {self.id}: vector tool_result needs target_tool")
        if self.vector == "user_prompt" and self.target_tool is not None:
            raise ValueError(f"attack {self.id}: vector user_prompt takes no target_tool")
        if self.target_tool is not None and self.target_tool not in TOOL_NAMES:
            raise ValueError(f"attack {self.id}: unknown tool {self.target_tool!r}")
        return self

    @property
    def rendered_payload(self) -> str:
        return "\n".join([self.payload] * self.payload_repeat)


class Dataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    benign: list[BenignTask] = Field(default_factory=list)
    attacks: list[Attack] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Dataset:
        ids = [t.id for t in self.benign] + [a.id for a in self.attacks]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate ids: {duplicates}")
        benign_ids = {t.id for t in self.benign}
        for attack in self.attacks:
            if attack.benign_task not in benign_ids:
                raise ValueError(f"attack {attack.id}: unknown benign task {attack.benign_task!r}")
        return self

    def benign_task(self, task_id: str) -> BenignTask:
        return next(t for t in self.benign if t.id == task_id)

    def attack(self, attack_id: str) -> Attack:
        return next(a for a in self.attacks if a.id == attack_id)


def load_dataset(data_dir: Path = DATA_DIR) -> Dataset:
    """Merge every ``*.yaml`` file in ``data_dir`` into one validated dataset."""
    benign: list[object] = []
    attacks: list[object] = []
    for path in sorted(data_dir.glob("*.yaml")):
        content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        benign += content.get("benign", [])
        attacks += content.get("attacks", [])
    return Dataset.model_validate({"benign": benign, "attacks": attacks})
