"""Attack dataset: benign tasks and the attacks paired with them."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dowbench.agent.tools import TOOL_NAMES

DATA_DIR = Path(__file__).with_name("data")

Family = Literal["tool_loop", "output_flood", "reasoning_bomb", "context_bloat", "mcp_chain"]
Vector = Literal["user_prompt", "tool_result", "tool_description"]
# What a susceptible agent does when the attack works. The mock provider imitates it.
Signal = Literal[
    "repeated_identical_calls",
    "repeated_tool_calls",
    "long_output",
    "long_reasoning",
    "context_growth",
    "alternating_tool_calls",
    "growing_arguments",
]

# Per-call placeholders a tool_result payload may use (ADR 0016): ``{n}`` is the 1-based
# index of the call to an injected tool, ``{next}`` is ``n + 1``.
PLACEHOLDERS = ("{n}", "{next}")
_MIN_MARKER = 16


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
    # Extra repeats per later call: call ``n`` repeats the payload
    # ``payload_repeat + payload_growth * (n - 1)`` times (ADR 0016).
    payload_growth: int = Field(default=0, ge=0, le=1000)
    target_tool: str | None = None
    # A second tool whose results carry the same payload, sharing the call counter.
    relay_tool: str | None = None
    expected_signal: Signal
    source: str = Field(min_length=1, description="Where the attack pattern comes from")

    @model_validator(mode="after")
    def _target_tool_matches_vector(self) -> Attack:
        needs_target = self.vector in ("tool_result", "tool_description")
        if needs_target and self.target_tool is None:
            raise ValueError(f"attack {self.id}: vector {self.vector} needs target_tool")
        if not needs_target and self.target_tool is not None:
            raise ValueError(f"attack {self.id}: vector {self.vector} takes no target_tool")
        if self.target_tool is not None and self.target_tool not in TOOL_NAMES:
            raise ValueError(f"attack {self.id}: unknown tool {self.target_tool!r}")
        return self

    @model_validator(mode="after")
    def _per_call_fields_need_tool_result(self) -> Attack:
        per_call = (
            self.payload_growth > 0
            or self.relay_tool is not None
            or any(p in self.payload for p in PLACEHOLDERS)
        )
        if per_call and self.vector != "tool_result":
            raise ValueError(
                f"attack {self.id}: placeholders, payload_growth and relay_tool "
                "need vector tool_result"
            )
        if self.relay_tool is not None and (
            self.relay_tool not in TOOL_NAMES or self.relay_tool == self.target_tool
        ):
            raise ValueError(f"attack {self.id}: bad relay_tool {self.relay_tool!r}")
        if self.marker != self.payload and len(self.marker.strip()) < _MIN_MARKER:
            raise ValueError(
                f"attack {self.id}: payload needs {_MIN_MARKER}+ characters before its "
                "first placeholder"
            )
        return self

    def render(self, n: int = 1) -> str:
        """The payload as it appears on the ``n``-th call to an injected tool."""
        text = self.payload.replace("{next}", str(n + 1)).replace("{n}", str(n))
        return "\n".join([text] * (self.payload_repeat + self.payload_growth * (n - 1)))

    @property
    def rendered_payload(self) -> str:
        return self.render(1)

    @property
    def marker(self) -> str:
        """Payload text before its first placeholder: the same on every call, so the mock
        provider can recognise the attack in its context."""
        cut = min(
            (i for p in PLACEHOLDERS if (i := self.payload.find(p)) >= 0),
            default=len(self.payload),
        )
        return self.payload[:cut]


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
