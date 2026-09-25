import socket

import pytest

from tests.conftest import NetworkAccessError


def test_outbound_connections_fail() -> None:
    with pytest.raises(NetworkAccessError):
        socket.create_connection(("example.com", 443), timeout=1)


def test_raw_socket_connect_fails() -> None:
    with socket.socket() as s, pytest.raises(NetworkAccessError):
        s.connect(("127.0.0.1", 9))
