#!/usr/bin/env python3
"""Assert the vulnerable -> mitigated loop still holds on the deterministic mock benchmark.

Run after ``dowbench run configs/pilot.yaml``. It reads the written ``summary.json`` and checks
the *mechanism*, not any particular number: the undefended attack amplifies past the success
threshold, and at least one defense brings the median amplification down. Mock numbers are
SIMULATED and must never be published (CLAUDE.md); this guards the pipeline, so a change that
breaks the attack->defense loop fails CI offline, with no key and no spend.

Usage: ``python scripts/check_mock_benchmark.py <run_dir>``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def check(run_dir: Path) -> int:
    summary_path = run_dir / "summary.json"
    if not summary_path.is_file():
        return _fail(f"no summary.json at {summary_path}; run `dowbench run` first")
    summary: dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))

    if not summary.get("simulated"):
        return _fail("mock run is not marked simulated; mock numbers must never read as real")

    threshold = summary["success_threshold"]
    by_defense = {d["defense"]: d for d in summary["defenses"]}
    if "none" not in by_defense:
        return _fail("no undefended ('none') baseline in the summary")

    undefended = by_defense["none"]["median_amplification"]
    if undefended is None or undefended < threshold:
        return _fail(
            f"undefended median amplification {undefended} did not reach the success "
            f"threshold {threshold}: the attack did not amplify, so the loop is broken"
        )

    mitigations = {
        name: d["median_amplification"]
        for name, d in by_defense.items()
        if name != "none" and d["median_amplification"] is not None
    }
    reducing = {name: amp for name, amp in mitigations.items() if amp < undefended}
    if not reducing:
        return _fail(
            f"no defense reduced the median amplification below the undefended {undefended:.1f}x; "
            "a mitigation should measurably lower cost"
        )

    # Every benign task must still complete under each defense, or the comparison is unfair.
    for name, d in by_defense.items():
        rate = d["benign_completion_rate"]
        if rate is not None and rate < 1.0:
            return _fail(f"defense '{name}' did not complete all benign tasks (rate {rate})")

    best = min(reducing, key=lambda k: reducing[k])
    print(
        f"OK (SIMULATED mock): undefended median A = {undefended:.1f}x >= {threshold:g}; "
        f"{len(reducing)}/{len(mitigations)} defenses reduced it, best '{best}' to "
        f"{reducing[best]:.1f}x; all benign tasks completed."
    )
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        return _fail("usage: check_mock_benchmark.py <run_dir>")
    return check(Path(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
