"""Offline absolute header deadlines, including byte-at-a-time trickles."""

from http.client import HTTPResponse
import io
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from urllib3.connection import HTTPSConnection

from pyicloud.mcp.header_deadline import (
    DeadlineRaw,
    DeadlineSocket,
    HeaderDeadlineExceeded,
    deadline_pool,
)


@pytest.mark.parametrize("trickle", [False, True])
def test_header_read_enforces_absolute_budget(trickle: bool) -> None:
    """Continuous header progress must not renew a total deadline."""
    sock = MagicMock()
    data = bytearray(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    clock = [0.0]
    raw = MagicMock()

    def readinto(buffer: bytearray) -> int:
        size = min(len(buffer), 1 if trickle else len(data), len(data))
        buffer[:size] = data[:size]
        del data[:size]
        clock[0] += 1
        return size

    raw.readinto.side_effect = readinto
    sock.makefile.return_value = raw
    with patch(
        "pyicloud.mcp.header_deadline.time.monotonic", side_effect=lambda: clock[0]
    ):
        response = HTTPResponse(cast(Any, DeadlineSocket(sock, 3)))
        try:
            if trickle:
                with pytest.raises(HeaderDeadlineExceeded):
                    response.begin()
                assert sock.settimeout.call_count == 3
            else:
                response.begin()
                assert response.status == 200
        finally:
            response.close()
    raw.close.assert_called()


def test_expired_raw_read_never_touches_socket() -> None:
    """An expired deadline fails before any additional socket read."""
    raw, sock = MagicMock(), MagicMock()
    wrapper = DeadlineRaw(raw, sock, 0)
    with (
        patch("pyicloud.mcp.header_deadline.time.monotonic", return_value=1),
        pytest.raises(HeaderDeadlineExceeded),
    ):
        wrapper.readinto(bytearray(1))
    raw.readinto.assert_not_called()
    sock.settimeout.assert_not_called()
    wrapper.close()


def test_socket_proxy_preserves_other_socket_methods() -> None:
    """Socket methods and successful raw reads retain their normal behavior."""
    sock = MagicMock()
    assert DeadlineSocket(sock, 10).gettimeout() == sock.gettimeout.return_value
    raw = DeadlineRaw(io.BytesIO(b"data"), sock, float("inf"))
    assert raw.readable()
    assert raw.readinto(bytearray(4)) == 4
    raw.close()


@pytest.mark.parametrize("fail", [False, True])
def test_pool_connection_restores_original_socket(fail: bool) -> None:
    """Neither success nor header failure leaves the connection socket wrapped."""
    pool = deadline_pool(lambda: 100)
    connection = cast(Any, pool.ConnectionCls("example.test"))
    original = MagicMock()
    connection.sock = original

    def response(_connection: HTTPSConnection) -> str:
        assert isinstance(_connection.sock, DeadlineSocket)
        if fail:
            raise OSError("Synthetic header failure")
        return "response"

    with patch.object(HTTPSConnection, "getresponse", response):
        if fail:
            with pytest.raises(OSError):
                connection.getresponse()
        else:
            assert connection.getresponse() == "response"
    assert connection.sock is original
    connection.close()


def test_missing_pool_connection_is_known_failure() -> None:
    """A disconnected pooled connection cannot start reading headers."""
    connection = deadline_pool(lambda: 100).ConnectionCls("example.test")
    with pytest.raises(HeaderDeadlineExceeded):
        connection.getresponse()
