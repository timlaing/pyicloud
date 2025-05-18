"""Base service."""

from abc import ABC
from typing import Any

from pyicloud.session import PyiCloudSession


class BaseService(ABC):
    """The base iCloud service."""

    def __init__(
        self, service_root: str, session: PyiCloudSession, params: dict[str, Any]
    ) -> None:
        self.__session: PyiCloudSession = session
        self.__params: dict[str, Any] = params
        self.__service_root: str = service_root

    @property
    def session(self) -> PyiCloudSession:
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
