"""Absolute response-header deadlines at the socket file's raw-read boundary."""

# cspell:words connectionpool

from collections.abc import Callable
import io
import time
from typing import Any, cast

from requests.exceptions import Timeout
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool


class HeaderDeadlineExceeded(Timeout):
    """The absolute budget expired while acquiring response headers."""


class DeadlineRaw(io.RawIOBase):
    """Recompute the remaining budget for every underlying header/body read."""

    def __init__(self, raw: Any, sock: Any, deadline: float) -> None:
        super().__init__()
        self.raw = raw
        self.sock = sock
        self.deadline = deadline

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int | None:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise HeaderDeadlineExceeded("Response deadline exceeded")
        self.sock.settimeout(remaining)
        size: int | None = self.raw.readinto(buffer)
        if time.monotonic() >= self.deadline:
            raise HeaderDeadlineExceeded("Response deadline exceeded")
        return size

    def close(self) -> None:
        try:
            self.raw.close()
        finally:
            super().close()


class DeadlineSocket:
    """Only intercept makefile; TLS I/O and socket management remain unchanged."""

    def __init__(self, sock: Any, deadline: float) -> None:
        self.sock = sock
        self.deadline = deadline

    def __getattr__(self, name: str) -> Any:
        return getattr(self.sock, name)

    def makefile(self, mode: str, buffering: int | None = None) -> io.BufferedReader:
        """Make deadline-aware buffered input over the original raw socket file."""
        del buffering
        raw = self.sock.makefile(mode, buffering=0)
        return io.BufferedReader(DeadlineRaw(raw, self.sock, self.deadline))


def deadline_pool(provider: Callable[[], float]) -> type[HTTPSConnectionPool]:
    """Build an adapter-local pool, never modify urllib3's global pool classes."""

    class DeadlineConnection(HTTPSConnection):
        """Install the deadline wrapper only while acquiring the HTTP response."""

        def getresponse(self) -> Any:
            original = self.sock
            if original is None:
                raise HeaderDeadlineExceeded("Connection unavailable")
            self.sock = cast(Any, DeadlineSocket(original, provider()))
            try:
                return HTTPSConnection.getresponse(self)
            finally:
                self.sock = original

    class DeadlinePool(HTTPSConnectionPool):
        """Adapter-local HTTPS pool using the header-aware connection class."""

        ConnectionCls = DeadlineConnection

    return DeadlinePool
