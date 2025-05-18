"""Unit tests for UbiquityService and UbiquityNode classes."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from requests import Response

from pyicloud.exceptions import PyiCloudAPIResponseException, PyiCloudServiceUnavailable
from pyicloud.services.ubiquity import UbiquityNode, UbiquityService
from pyicloud.session import PyiCloudSession


def test_ubiquity_service_init() -> None:
    """Test UbiquityService initialization and exception handling."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_session.get.return_value = MagicMock(
        spec=Response, json=lambda: {"item_list": []}
    )
    params: dict[str, str] = {"dsid": "12345"}

    # Test successful initialization
    service = UbiquityService("https://example.com", mock_session, params)
    assert service.service_root == "https://example.com"
    assert service.params == params

    # Test exception handling
    mock_session.get.side_effect = PyiCloudAPIResponseException(
        code=503, reason="Service Unavailable"
    )
    with pytest.raises(PyiCloudServiceUnavailable):
        UbiquityService("https://example.com", mock_session, params)


def test_ubiquity_service_root() -> None:
    """Test the root property of UbiquityService."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_session.get.return_value = MagicMock(
        spec=Response, json=lambda: {"item_id": "0"}
    )
    service = UbiquityService("https://example.com", mock_session, {"dsid": "12345"})

    root: UbiquityNode = service.root
    assert isinstance(root, UbiquityNode)
    assert root.item_id == "0"


def test_get_node_url() -> None:
    """Test get_node_url method."""
    service = UbiquityService("https://example.com", MagicMock(), {"dsid": "12345"})
    url: str = service.get_node_url("node123")
    assert url == "https://example.com/ws/12345/item/node123"


def test_get_node() -> None:
    """Test get_node method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"item_id": "123"}
    mock_session.get.return_value = mock_response

    service = UbiquityService("https://example.com", mock_session, {"dsid": "12345"})
    node: UbiquityNode = service.get_node("123")
    assert isinstance(node, UbiquityNode)
    assert node.item_id == "123"


def test_get_children() -> None:
    """Test get_children method."""
    mock_session = MagicMock(spec=PyiCloudSession)
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {
        "item_list": [{"item_id": "1"}, {"item_id": "2"}]
    }
    mock_session.get.return_value = mock_response

    service = UbiquityService("https://example.com", mock_session, {"dsid": "12345"})
    children: list[UbiquityNode] = service.get_children("123")
    assert len(children) == 2
    assert all(isinstance(child, UbiquityNode) for child in children)


def test_ubiquity_node_properties() -> None:
    """Test UbiquityNode properties."""
    data: dict[str, str] = {
        "item_id": "123",
        "name": "Test Node",
        "type": "folder",
        "size": "1024",
        "modified": "2023-01-01T12:00:00Z",
    }
    node = UbiquityNode(MagicMock(), data)

    assert node.item_id == "123"
    assert node.name == "Test Node"
    assert node.type == "folder"
    assert node.size == 1024
    assert node.modified == datetime(2023, 1, 1, 12, 0, 0)


def test_ubiquity_node_get_children() -> None:
    """Test UbiquityNode get_children method."""
    mock_service = MagicMock(spec=UbiquityService)
    mock_service.get_children.return_value = [MagicMock(spec=UbiquityNode)]
    node = UbiquityNode(mock_service, {"item_id": "123"})

    children: list[UbiquityNode] = node.get_children()
    assert len(children) == 1
    assert isinstance(children[0], UbiquityNode)


def test_ubiquity_node_dir() -> None:
    """Test UbiquityNode dir method."""
    mock_child = MagicMock(spec=UbiquityNode)
    mock_child.name = "Child Node"
    mock_service = MagicMock(spec=UbiquityService)
    mock_service.get_children.return_value = [mock_child]
    node = UbiquityNode(mock_service, {"item_id": "123"})

    directories: list[str] = node.dir()
    assert directories == ["Child Node"]


def test_ubiquity_node_get() -> None:
    """Test UbiquityNode get method."""
    mock_child = MagicMock(spec=UbiquityNode, name="Child Node")
    mock_child.name = "Child Node"
    mock_service = MagicMock(spec=UbiquityService)
    mock_service.get_children.return_value = [mock_child]
    node = UbiquityNode(mock_service, {"item_id": "123"})

    child: UbiquityNode = node.get("Child Node")
    assert child == mock_child


def test_ubiquity_node_getitem() -> None:
    """Test UbiquityNode __getitem__ method."""
    mock_child = MagicMock(spec=UbiquityNode, name="Child Node")
    mock_child.name = "Child Node"
    mock_service = MagicMock(spec=UbiquityService)
    mock_service.get_children.return_value = [mock_child]
    node = UbiquityNode(mock_service, {"item_id": "123"})

    child: UbiquityNode = node["Child Node"]
    assert child == mock_child
