"""Append-only JSONL storage for a run: one row per model call and one per episode.

Calls are written as they return; the episode row marks the episode as complete. Call rows
whose ``attempt`` has no episode row belong to an interrupted attempt (ADR 0007).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from dowbench.agent.loop import CallRecord, EpisodeStatus
from dowbench.metering.usage import Usage

# Anything that could carry a credential is dropped or masked before a request is written
# to disk (ADR 0009). Bodies come from the adapters, which never put keys in them; this is
# the second line of defense at the single place requests leave memory.
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "key",
        "authorization",
        "x-api-key",
        "x-goog-api-key",
        "headers",
        "http_options",
        "auth_token",
        "access_token",
    }
)
_SECRET_VALUE = re.compile(r"AIza[0-9A-Za-z_-]{20,}|AQ\.[0-9A-Za-z_-]{10,}|sk-ant-[0-9A-Za-z_-]+")
REDACTED = "[REDACTED]"


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if str(k).lower() not in _SECRET_KEYS}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(REDACTED, value)
    return value


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
    cost_usd: float | None  # None for unpriced runs (ADR 0008)
    simulated: bool
    attempt: str = ""


class RunStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.calls_path = run_dir / "calls.jsonl"
        self.requests_path = run_dir / "requests.jsonl"
        self.episodes_path = run_dir / "episodes.jsonl"
        self.cassette_path = run_dir / "cassette.jsonl"
        self.run_path = run_dir / "run.json"
        self.summary_path = run_dir / "summary.json"

    def load_episodes(self) -> list[EpisodeRecord]:
        if not self.episodes_path.exists():
            return []
        with self.episodes_path.open(encoding="utf-8") as fh:
            return [EpisodeRecord.model_validate_json(line) for line in fh if line.strip()]

    def append_call(self, context: dict[str, str], call: CallRecord) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        # newline="\n" so a run's files are byte-for-byte identical on every OS, which the
        # replay reproduction (ADR 0015) and any result hash rely on; the default would write
        # CRLF on Windows.
        with self.calls_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({**context, **call.model_dump(mode="json")}, sort_keys=True) + "\n")
        if call.request_body:
            row = {**context, "turn": call.turn, "request": sanitize(call.request_body)}
            with self.requests_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")

    def append_cassette(self, row: dict[str, Any]) -> None:
        """Append one recorded call to cassette.jsonl, sanitized (ADR 0009, ADR 0015)."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.cassette_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(sanitize(row), sort_keys=True) + "\n")

    def load_cassette(self) -> list[dict[str, Any]]:
        if not self.cassette_path.exists():
            return []
        with self.cassette_path.open(encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def served_model_versions(self) -> dict[str, list[str]]:
        """Model versions the provider reported, per requested model, across all attempts."""
        served: dict[str, set[str]] = {}
        if self.calls_path.exists():
            with self.calls_path.open(encoding="utf-8") as fh:
                for line in fh:
                    row = json.loads(line)
                    if row.get("model_version"):
                        served.setdefault(row["model"], set()).add(row["model_version"])
        return {model: sorted(versions) for model, versions in sorted(served.items())}

    def append_episode(self, episode: EpisodeRecord) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with self.episodes_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(episode.model_dump_json() + "\n")

    def write_json(self, path: Path, model: BaseModel) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
