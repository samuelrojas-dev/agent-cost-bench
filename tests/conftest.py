"""Test-suite guards: no test may touch the network (tests must never consume provider quota)."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from typing import Any

import pytest


class NetworkAccessError(RuntimeError):
    pass


def _blocked(*_args: Any, **_kwargs: Any) -> Any:
    raise NetworkAccessError("network access is forbidden in tests; use the mock provider")


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    yield
