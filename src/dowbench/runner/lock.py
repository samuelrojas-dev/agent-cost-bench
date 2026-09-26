"""One run at a time per run directory (ADR 0010).

The lock is a file created atomically with O_EXCL, so it works on any OS and filesystem
that honours O_EXCL. A process that dies without releasing it leaves the file behind; the
run then refuses to start until someone who knows no run is active deletes it. Guessing
that a lock is stale could let two runs spend in parallel, so it is never done here.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
from pathlib import Path
from types import TracebackType

LOCK_NAME = "run.lock"


class RunLockedError(RuntimeError):
    pass


class RunLock:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / LOCK_NAME

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            raise RunLockedError(
                f"{self.path} exists: another run may be using this directory "
                f"({self._holder()}). If no run is active, delete the file and retry."
            ) from None
        holder = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        }
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(holder, fh)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.path.unlink(missing_ok=True)

    def _holder(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return f"pid {data['pid']} on {data['host']} since {data['started']}"
        except (OSError, ValueError, KeyError, TypeError):
            return "holder unknown"
