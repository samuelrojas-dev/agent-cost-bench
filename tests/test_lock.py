import json
from pathlib import Path

import pytest

from dowbench.runner.lock import LOCK_NAME, RunLock, RunLockedError


def test_second_holder_is_refused_and_told_who_holds_it(tmp_path: Path) -> None:
    with RunLock(tmp_path):
        holder = json.loads((tmp_path / LOCK_NAME).read_text())
        assert {"pid", "host", "started"} <= holder.keys()
        with pytest.raises(RunLockedError, match=f"pid {holder['pid']}"):
            RunLock(tmp_path).__enter__()
    assert not (tmp_path / LOCK_NAME).exists()


def test_lock_is_released_when_the_run_fails(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), RunLock(tmp_path):
        raise RuntimeError("boom")
    with RunLock(tmp_path):  # can be taken again
        pass


def test_leftover_lock_is_never_treated_as_stale(tmp_path: Path) -> None:
    (tmp_path / LOCK_NAME).write_text("not json")
    with pytest.raises(RunLockedError, match="holder unknown"):
        RunLock(tmp_path).__enter__()
    assert (tmp_path / LOCK_NAME).exists()  # left for a human to remove
