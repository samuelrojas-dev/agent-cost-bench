"""Regenerate the committed synthetic replay fixture (ADR 0015 §6).

The fixture is a tiny, offline, non-simulated-mock recording — no real provider bytes. Run
this whenever the request construction changes and ``test_committed_fixture_cassette_replays_
to_its_recorded_summary`` fails on a drift (that failure is the digest guard doing its job):

    python -m tests.fixtures.make_cassette_demo

It records one benign episode with a mock that reports itself as real, then keeps only the
files replay needs: ``run.json``, ``cassette.jsonl`` and the reference ``summary.json``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from dowbench.attacks.schema import load_dataset
from dowbench.metering.pricing import PriceTable
from dowbench.runner.config import RunConfig
from dowbench.runner.execute import execute

FIXTURE = Path(__file__).parent / "cassette-demo"
PILOT = Path(__file__).parent.parent.parent / "configs" / "pilot.yaml"


def _config() -> RunConfig:
    data = RunConfig.from_yaml(PILOT).model_dump(mode="json")
    task = load_dataset().benign[0].id
    data.update(
        {"attacks": [], "benign_tasks": [task], "defenses": [{"name": "none"}], "repeats": 1}
    )
    return RunConfig.model_validate(data)


def main() -> None:
    from tests.test_replay import RealishProvider  # the same non-simulated mock the tests use

    config, dataset = _config(), load_dataset()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        execute(config, dataset, provider=RealishProvider(), prices=PriceTable.load(), out_dir=out)
        run_dir = out / config.run_name
        FIXTURE.mkdir(parents=True, exist_ok=True)
        # episodes.jsonl is needed too: replay matches the cassette by (episode_id, attempt)
        # and reads it to keep only the completed attempt (ADR 0015).
        for name in ("run.json", "cassette.jsonl", "episodes.jsonl", "summary.json"):
            shutil.copyfile(run_dir / name, FIXTURE / name)
    print(f"wrote fixture to {FIXTURE}")


if __name__ == "__main__":
    main()
