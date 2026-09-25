"""Expand a run config into episodes with stable ids (used to resume)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from dowbench.attacks.schema import Attack, Dataset
from dowbench.defenses import DefenseSpec
from dowbench.runner.config import RunConfig


def _digest(data: Any) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EpisodeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    model: str
    defense: DefenseSpec
    kind: Literal["benign", "attack"]
    item_id: str
    benign_task_id: str
    repeat: int
    seed: int
    # Hash of everything else that changes the episode: provider, prompt, ceiling, item text.
    context: str

    @property
    def id(self) -> str:
        return _digest(self.model_dump(mode="json"))[:16]


def select_attacks(config: RunConfig, dataset: Dataset) -> list[Attack]:
    if config.attacks == "all":
        return list(dataset.attacks)
    known = {a.id for a in dataset.attacks}
    unknown = sorted(set(config.attacks) - known)
    if unknown:
        raise ValueError(f"unknown attack ids in config: {unknown}")
    return [dataset.attack(attack_id) for attack_id in config.attacks]


def plan_episodes(config: RunConfig, dataset: Dataset) -> list[EpisodeSpec]:
    attacks = select_attacks(config, dataset)
    needed = {a.benign_task for a in attacks}
    benign = [task for task in dataset.benign if task.id in needed]
    shared = {
        "provider": config.provider,
        "system_prompt": config.system_prompt,
        "ceiling": config.ceiling.model_dump(mode="json"),
    }
    items: list[tuple[Literal["benign", "attack"], str, str, str]] = [
        ("benign", t.id, t.id, _digest({**shared, "item": t.model_dump(mode="json")}))
        for t in benign
    ] + [
        ("attack", a.id, a.benign_task, _digest({**shared, "item": a.model_dump(mode="json")}))
        for a in attacks
    ]
    return [
        EpisodeSpec(
            model=model,
            defense=defense,
            kind=kind,
            item_id=item_id,
            benign_task_id=benign_task_id,
            repeat=repeat,
            seed=config.seed,
            context=context,
        )
        for model in config.models
        for defense in config.defenses
        for repeat in range(config.repeats)
        for kind, item_id, benign_task_id, context in items
    ]
