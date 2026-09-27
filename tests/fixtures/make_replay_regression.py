"""Regenerate the replay regression fixture (ADR 0015).

A tiny, offline, sanitized cassette that exercises both replay bugs at once:

- its stored config carries a ``rate_limit`` (so a buggy replay would wrap the provider in
  the rate limiter and sleep / drift), and
- it contains an **orphan attempt**: a copy of the completed episode's first ``count`` call
  under a different attempt id, placed first, with no matching episode row (so a buggy
  ``load_cassette`` that groups by ``episode_id`` alone splices it in and drifts).

Regenerate when request construction changes and the regression test drifts:

    python -m tests.fixtures.make_replay_regression
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from dowbench.attacks.schema import load_dataset
from dowbench.metering.pricing import PriceTable
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import execute

FIXTURE = Path(__file__).parent / "replay-regression"
PILOT = Path(__file__).parent.parent.parent / "configs" / "pilot.yaml"
ORPHAN_ATTEMPT = "0000orphanattempt00000000000000"  # deliberately not in episodes.jsonl


def _config() -> RunConfig:
    data = RunConfig.from_yaml(PILOT).model_dump(mode="json")
    task = load_dataset().benign[0].id
    data.update(
        {"attacks": [], "benign_tasks": [task], "defenses": [{"name": "none"}], "repeats": 1}
    )
    return RunConfig.model_validate(data)


def main() -> None:
    from tests.test_replay import RealishProvider  # a non-simulated mock, so a cassette is written

    config, dataset = _config(), load_dataset()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        # Record with no rate_limit so generation itself never sleeps on real time.
        execute(config, dataset, provider=RealishProvider(), prices=PriceTable.load(), out_dir=out)
        run_dir = out / config.run_name

        # 1) Store a tight rate_limit in the config: rpm=1 would force a real sleep if a buggy
        #    replay wrapped the provider in the limiter. It never affects the summary.
        info = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        info["config"]["rate_limit"] = {"rpm": 1, "tpm": 1000, "rpd": 1000}
        (run_dir / "run.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")

        # 2) Inject an orphan attempt: the completed attempt's first `count` call copied under a
        #    different attempt id and placed first. Its digest is valid (same request), so a
        #    buggy episode-only match consumes it and then drifts on the next call's kind.
        rows = [json.loads(x) for x in (run_dir / "cassette.jsonl").read_text().splitlines()]
        first_count = next(r for r in rows if r["kind"] == "count")
        orphan = json.loads(json.dumps(first_count))
        orphan["attempt"] = ORPHAN_ATTEMPT
        cassette = [orphan, *rows]
        (run_dir / "cassette.jsonl").write_text(
            "\n".join(json.dumps(r, sort_keys=True) for r in cassette) + "\n", encoding="utf-8"
        )

        FIXTURE.mkdir(parents=True, exist_ok=True)
        for name in ("run.json", "cassette.jsonl", "episodes.jsonl", "summary.json"):
            shutil.copyfile(run_dir / name, FIXTURE / name)
    print(f"wrote fixture to {FIXTURE}")


if __name__ == "__main__":
    main()
