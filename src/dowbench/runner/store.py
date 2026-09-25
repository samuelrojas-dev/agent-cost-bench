"""Append-only JSONL storage for a run: one row per model call and one per episode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from dowbench.agent.loop import CallRecord, EpisodeStatus
from dowbench.metering.usage import Usage


class EpisodeRecord(BaseModel):
    episode_id: str
    model: str
    defense: str
    kind: Literal["benign", "attack"]
    item_id: str
    benign_task_id: str
    repeat: int
    status: EpisodeStatus
    reason: str | None
    turns: int
    usage: Usage
    cost_usd: float
    simulated: bool


class RunStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.calls_path = run_dir / "calls.jsonl"
        self.episodes_path = run_dir / "episodes.jsonl"
        self.run_path = run_dir / "run.json"
        self.summary_path = run_dir / "summary.json"

    def load_episodes(self) -> list[EpisodeRecord]:
        if not self.episodes_path.exists():
            return []
        with self.episodes_path.open(encoding="utf-8") as fh:
            return [EpisodeRecord.model_validate_json(line) for line in fh if line.strip()]

    def append(self, episode: EpisodeRecord, calls: list[CallRecord]) -> None:
        """Write the calls first; the episode row marks the episode as complete."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        context = {"episode_id": episode.episode_id, "model": episode.model}
        with self.calls_path.open("a", encoding="utf-8") as fh:
            for call in calls:
                row = {**context, **call.model_dump(mode="json")}
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        with self.episodes_path.open("a", encoding="utf-8") as fh:
            fh.write(episode.model_dump_json() + "\n")

    def write_json(self, path: Path, model: BaseModel) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
