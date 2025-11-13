"""Async base service."""

from abc import ABC
from typing import Any

from pyicloud.async_session import AsyncPyiCloudSession


class AsyncBaseService(ABC):
    """The base async iCloud service."""

    def __init__(
        self, service_root: str, session: AsyncPyiCloudSession, params: dict[str, Any]
    ) -> None:
        self.__session: AsyncPyiCloudSession = session
        self.__params: dict[str, Any] = params
        self.__service_root: str = service_root

    @property
    def session(self) -> AsyncPyiCloudSession:
        """The session object."""
        return self.__session

    @property
    def params(self) -> dict[str, Any]:
        """The request parameters."""
        return self.__params

    @property
    def service_root(self) -> str:
        """The service root URL."""
        return self.__service_root
