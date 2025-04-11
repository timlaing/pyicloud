"""File service."""

from datetime import datetime
from typing import Any, Optional

from requests import Response

from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudServiceUnavailable
from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession


class UbiquityService(BaseService):
    """The 'Ubiquity' iCloud service."""

    def __init__(
        self, service_root: str, session: PyiCloudSession, params: dict[str, Any]
    ) -> None:
        super().__init__(service_root, session, params)

        self._root: Optional["UbiquityNode"] = None

        try:
            self.root
        except PyiCloudAPIResponseException as error:
            if error.code == 503:
                raise PyiCloudServiceUnavailable(error.reason) from error
            raise

    @property
    def root(self) -> "UbiquityNode":
        """Gets the root node."""
        if not self._root:
            self._root = self.get_node(0)
        return self._root

    def get_node_url(self, node_id, variant="item") -> str:
        """Returns a node URL."""
        return f"{self.service_root}/ws/{self.params['dsid']}/{variant}/{node_id}"

    def get_node(self, node_id) -> "UbiquityNode":
        """Returns a node."""
        response: Response = self.session.get(self.get_node_url(node_id))
        return UbiquityNode(self, response.json())

    def get_children(self, node_id) -> list["UbiquityNode"]:
        """Returns a node children."""
        response: Response = self.session.get(self.get_node_url(node_id, "parent"))
        items: list[dict[str, str]] = response.json()["item_list"]
        return [UbiquityNode(self, item) for item in items]

    def get_file(self, node_id, **kwargs) -> Response:
        """Returns a node file."""
        return self.session.get(self.get_node_url(node_id, "file"), **kwargs)

    def __getattr__(self, attr):
        return getattr(self.root, attr)

    def __getitem__(self, key) -> "UbiquityNode":
        return self.root[key]


class UbiquityNode:
    """Ubiquity node."""

    def __init__(self, conn: UbiquityService, data: dict[str, str]) -> None:
        self.data: dict[str, str] = data
        self.connection: UbiquityService = conn

        self._children: Optional[list[UbiquityNode]] = None

    @property
    def item_id(self) -> Optional[str]:
        """Gets the node id."""
        return self.data.get("item_id")

    @property
    def name(self) -> str:
        """Gets the node name."""
        return self.data.get("name", "<unknown>")

    @property
    def type(self) -> str:
        """Gets the node type."""
        return self.data.get("type", "<unknown>")

    @property
    def size(self) -> Optional[int]:
        """Gets the node size."""
        try:
            return int(self.data.get("size", "-1"))
        except ValueError:
            return None

    @property
    def modified(self) -> datetime:
        """Gets the node modified date."""
        return datetime.strptime(self.data.get("modified", ""), "%Y-%m-%dT%H:%M:%SZ")

    def open(self, **kwargs) -> Response:
        """Returns the node file."""
        return self.connection.get_file(self.item_id, **kwargs)

    def get_children(self) -> list["UbiquityNode"]:
        """Returns the node children."""
        if not self._children:
            self._children = self.connection.get_children(self.item_id)
        return self._children

    def dir(self) -> list[str]:
        """Returns children node directories by their names."""
        return [child.name for child in self.get_children()]

    def get(self, name: str) -> "UbiquityNode":
        """Returns a child node by its name."""
        return [child for child in self.get_children() if child.name == name][0]

    def __getitem__(self, key: str) -> "UbiquityNode":
        try:
            return self.get(key)
        except IndexError as i:
            raise KeyError(f"No child named {key} exists") from i

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"<{self.type.capitalize()}: '{self}'>"
