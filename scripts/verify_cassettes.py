#!/usr/bin/env python3
"""Verify the published replay cassettes still reproduce their committed results.

For every run directory under ``results/cassettes/`` this:

1. replays it with ``dowbench replay`` (offline — no key, no provider call, no spend);
2. asserts the replayed ``summary.json`` hashes **byte for byte** to the committed
   ``summary.json`` in the cassette directory (this is the ADR 0015 guarantee);
3. asserts the README shows that same sha256 in its truncated ``<first8>…<last7>`` form,
   so the published numbers can never silently drift from the artifacts.

No hash is hard-coded here: the committed ``summary.json`` is the anchor, replay must
regenerate it from ``cassette.jsonl``, and the README is checked against it. Run it in CI
and locally; it exits non-zero on the first mismatch.

Usage: ``python scripts/verify_cassettes.py``
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASSETTES_DIR = REPO_ROOT / "results" / "cassettes"
README = REPO_ROOT / "README.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _truncated(digest: str) -> str:
    """The form the README uses, e.g. ``12abac6a…166dcad`` (U+2026 ellipsis)."""
    return f"{digest[:8]}…{digest[-7:]}"


def _run_dirs() -> list[Path]:
    return sorted(p for p in CASSETTES_DIR.iterdir() if (p / "run.json").is_file())


def verify() -> int:
    run_dirs = _run_dirs()
    if not run_dirs:
        print(f"no cassette run directories found under {CASSETTES_DIR}", file=sys.stderr)
        return 1

    readme_text = README.read_text(encoding="utf-8")
    failures = 0

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        for run_dir in run_dirs:
            name = run_dir.name
            committed = run_dir / "summary.json"
            reference = _sha256(committed)

            result = subprocess.run(
                ["dowbench", "replay", str(run_dir), "--out", str(out_dir)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"FAIL {name}: replay exited {result.returncode}\n{result.stderr}")
                failures += 1
                continue

            run_name = json.loads((run_dir / "run.json").read_text())["run_name"]
            replayed = out_dir / run_name / "summary.json"
            if not replayed.is_file():
                print(f"FAIL {name}: replay wrote no summary.json at {replayed}")
                failures += 1
                continue

            replayed_hash = _sha256(replayed)
            if replayed_hash != reference:
                print(
                    f"FAIL {name}: replay did not reproduce summary.json\n"
                    f"  committed: {reference}\n  replayed:  {replayed_hash}"
                )
                failures += 1
                continue

            short = _truncated(reference)
            if short not in readme_text:
                print(
                    f"FAIL {name}: README does not show hash {short} "
                    f"(full {reference}); update the README or the cassette."
                )
                failures += 1
                continue

            print(f"OK   {name}: replay reproduces {short}, and the README matches")

    if failures:
        print(f"\n{failures} cassette(s) failed verification", file=sys.stderr)
        return 1
    print(f"\nall {len(run_dirs)} published cassette(s) verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(verify())
