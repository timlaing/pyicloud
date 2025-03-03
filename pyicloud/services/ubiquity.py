"""File service."""

from datetime import datetime
from typing import Optional

from .base import BaseService


class UbiquityService(BaseService):
    """The 'Ubiquity' iCloud service."""

    def __init__(self, service_root, session, params) -> None:
        super().__init__(service_root, session, params)

        self._root: Optional["UbiquityNode"] = None
        self._node_url: str = service_root + "/ws/%s/%s/%s"

    @property
    def root(self) -> "UbiquityNode":
        """Gets the root node."""
        if not self._root:
            self._root = self.get_node(0)
        return self._root

    def get_node_url(self, node_id, variant="item") -> str:
        """Returns a node URL."""
        return self._node_url % (self.params["dsid"], variant, node_id)

    def get_node(self, node_id) -> "UbiquityNode":
        """Returns a node."""
        request = self.session.get(self.get_node_url(node_id))
        return UbiquityNode(self, request.json())

    def get_children(self, node_id) -> list["UbiquityNode"]:
        """Returns a node children."""
        request = self.session.get(self.get_node_url(node_id, "parent"))
        items = request.json()["item_list"]
        return [UbiquityNode(self, item) for item in items]

    def get_file(self, node_id, **kwargs):
        """Returns a node file."""
        return self.session.get(self.get_node_url(node_id, "file"), **kwargs)

    def __getattr__(self, attr):
        return getattr(self.root, attr)

    def __getitem__(self, key):
        return self.root[key]


class UbiquityNode:
    """Ubiquity node."""

    def __init__(self, conn, data) -> None:
        self.data = data
        self.connection = conn

        self._children = None

    @property
    def item_id(self) -> str:
        """Gets the node id."""
        return self.data.get("item_id")

    @property
    def name(self) -> str:
        """Gets the node name."""
        return self.data.get("name")

    @property
    def type(self) -> str:
        """Gets the node type."""
        return self.data.get("type")

    @property
    def size(self) -> Optional[int]:
        """Gets the node size."""
        try:
            return int(self.data.get("size"))
        except ValueError:
            return None

    @property
    def modified(self) -> datetime:
        """Gets the node modified date."""
        return datetime.strptime(self.data.get("modified"), "%Y-%m-%dT%H:%M:%SZ")

    def open(self, **kwargs):
        """Returns the node file."""
        return self.connection.get_file(self.item_id, **kwargs)

    def get_children(self):
        """Returns the node children."""
        if not self._children:
            self._children = self.connection.get_children(self.item_id)
        return self._children

    def dir(self):
        """Returns children node directories by their names."""
        return [child.name for child in self.get_children()]

    def get(self, name):
        """Returns a child node by its name."""
        return [child for child in self.get_children() if child.name == name][0]

    def __getitem__(self, key):
        try:
            return self.get(key)
        except IndexError as i:
            raise KeyError(f"No child named {key} exists") from i

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"<{self.type.capitalize()}: '{self}'>"
