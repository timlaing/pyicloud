"""Branch-coverage tests for the Drive and Calendar services (issue #394).

Targets previously-uncovered branches in ``pyicloud/services/drive.py`` and
``pyicloud/services/calendar.py``. Every HTTP/session interaction is replaced
with a ``unittest.mock`` double, so nothing here touches the network or the
filesystem.
"""

# pylint: disable=protected-access

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from requests import Response

from pyicloud.services.calendar import (
    CalendarObject,
    CalendarService,
    EventObject,
)
from pyicloud.services.drive import (
    CLOUD_DOCS_ZONE,
    DriveNode,
    DriveService,
)
from pyicloud.session import PyiCloudSession


@pytest.fixture(autouse=True)
def _fixed_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the process timezone so no tz database filesystem reads occur."""
    monkeypatch.setattr("pyicloud.services.calendar.get_localzone_name", lambda: "UTC")


def _drive_service() -> DriveService:
    """Build a Drive service whose session performs no I/O."""
    return DriveService(
        "https://example.com",
        "https://document.example.com",
        MagicMock(spec=PyiCloudSession),
        {"dsid": "1", "clientId": "cid"},
    )


def _calendar_service() -> CalendarService:
    """Build a Calendar service whose session performs no I/O."""
    return CalendarService(
        "https://example.com",
        MagicMock(spec=PyiCloudSession),
        {"dsid": "12345"},
    )


# ---------------------------------------------------------------------------
# drive.py -- DriveService request builders
# ---------------------------------------------------------------------------


def test_get_node_data_with_share_id() -> None:
    """A share id is folded into the node-data payload."""
    drive = _drive_service()
    with patch.object(
        drive.session,
        "post",
        return_value=Mock(ok=True, json=lambda: [{"name": "x"}]),
    ) as post:
        assert drive.get_node_data("id", share_id={"recordName": "r"}) == {"name": "x"}
    assert post.call_args.kwargs["json"][0]["shareID"] == {"recordName": "r"}


def test_get_file_with_owner_record_name() -> None:
    """A known owner is appended (URL-encoded) to the download path."""
    drive = _drive_service()
    with patch.object(
        drive.session,
        "get",
        side_effect=[
            Mock(ok=True, json=lambda: {"data_token": {"url": "https://dl"}}),
            Mock(ok=True, content=b"payload"),
        ],
    ) as get:
        response = drive.get_file("fid", owner_record_name="Owner Name")
    assert response.content == b"payload"
    assert get.call_args_list[0].args[0].endswith("/Owner%20Name")


def test_get_file_package_token_and_missing_tokens() -> None:
    """The package-token path is used, and its absence raises ``KeyError``."""
    drive = _drive_service()
    with patch.object(
        drive.session,
        "get",
        side_effect=[
            Mock(ok=True, json=lambda: {"package_token": {"url": "https://pkg"}}),
            Mock(ok=True, content=b"pkg"),
        ],
    ) as get:
        response = drive.get_file("fid")
    assert response.content == b"pkg"
    assert get.call_args_list[1].args[0] == "https://pkg"

    with (
        patch.object(drive.session, "get", return_value=Mock(ok=True, json=lambda: {})),
        pytest.raises(KeyError, match="package_token"),
    ):
        drive.get_file("fid")


def test_get_app_data_returns_items() -> None:
    """``get_app_data`` unwraps the ``items`` list from the response."""
    drive = _drive_service()
    with patch.object(
        drive.session,
        "get",
        return_value=Mock(ok=True, json=lambda: {"items": [{"name": "app"}]}),
    ) as get:
        assert drive.get_app_data() == [{"name": "app"}]
    assert get.call_args.args[0] == drive.service_root + "/retrieveAppLibraries"


def test_update_contentws_includes_receipt() -> None:
    """A file checksum receipt is forwarded on the update payload."""
    drive = _drive_service()
    file_object = Mock()
    file_object.name = "folder/data.bin"
    file_info: dict[str, Any] = {
        "fileChecksum": "sum",
        "wrappingKey": "key",
        "referenceChecksum": "ref",
        "size": 10,
        "receipt": {"receipt": "value"},
    }
    with patch.object(
        drive.session,
        "post",
        return_value=Mock(ok=True, json=lambda: {"status": "OK"}),
    ) as post:
        assert drive._update_contentws("folder", file_info, "doc", file_object) == {
            "status": "OK"
        }
    payload = post.call_args.kwargs["json"]
    assert payload["data"]["receipt"] == {"receipt": "value"}
    assert payload["path"]["path"] == "data.bin"


def test_move_nodes_to_node_payload() -> None:
    """Moving multiple nodes sends matching client ids for each item."""
    drive = _drive_service()
    first = DriveNode(drive, {"drivewsid": "A", "etag": "e1"})
    second = DriveNode(drive, {"drivewsid": "B", "etag": "e2"})
    destination = DriveNode(drive, {"drivewsid": "DEST"})
    with patch.object(
        drive.session,
        "post",
        return_value=Mock(ok=True, json=lambda: {"items": []}),
    ) as post:
        assert drive.move_nodes_to_node([first, second], destination) == {"items": []}
    payload = post.call_args.kwargs["json"]
    assert payload["destinationDrivewsId"] == "DEST"
    assert [item["clientId"] for item in payload["items"]] == ["A", "B"]


def test_root_raises_when_refresh_leaves_it_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``root`` raises ``ValueError`` if refreshing did not populate it."""
    drive = _drive_service()
    monkeypatch.setattr(drive, "refresh_root", MagicMock())
    with pytest.raises(ValueError, match="Root not found"):
        _ = drive.root


def test_trash_property_returns_cached_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``trash`` returns a cached node or raises when refresh fails."""
    drive = _drive_service()
    trash = DriveNode(drive, {"name": "trash"})
    drive._trash = trash
    assert drive.trash is trash

    drive._trash = None
    monkeypatch.setattr(drive, "refresh_trash", MagicMock())
    with pytest.raises(ValueError, match="Trash not found"):
        _ = drive.trash


def test_node_name_and_type_fallbacks() -> None:
    """Nodes without a name or type fall back to the service constants."""
    drive = _drive_service()
    assert DriveNode(drive, {}).name == DriveNode.NAME_UNKNOWN
    assert DriveNode(drive, {}).type == DriveNode.TYPE_UNKNOWN
    assert DriveNode(drive, {"drivewsid": "TRASH_ROOT"}).type == DriveNode.TYPE_TRASH


def test_node_get_children_uses_cache() -> None:
    """A populated child cache is returned without hitting the service."""
    drive = _drive_service()
    child = DriveNode(drive, {"name": "c"})
    parent = DriveNode(drive, {"name": "p"})
    parent._children = [child]
    assert parent.get_children() == [child]


def test_node_remove_child_and_empty_error() -> None:
    """``remove`` prunes both the payload and cache, or raises when empty."""
    drive = _drive_service()
    parent = DriveNode(
        drive,
        {"name": "p", "items": [{"docwsid": "c1"}, {"docwsid": "c2"}]},
    )
    child_one = DriveNode(drive, {"docwsid": "c1"})
    child_two = DriveNode(drive, {"docwsid": "c2"})
    parent._children = [child_one, child_two]

    parent.remove(child_one)

    assert parent.data["items"] == [{"docwsid": "c2"}]
    assert parent._children == [child_two]

    lone = DriveNode(drive, {})
    with pytest.raises(ValueError, match="No children to remove"):
        lone.remove(child_one)


def test_node_remove_skips_non_matching_and_empty_items() -> None:
    """``remove`` tolerates non-matching entries and an empty item list."""
    drive = _drive_service()

    skipless = DriveNode(drive, {"name": "p", "items": [{"docwsid": "other"}]})
    child = DriveNode(drive, {"docwsid": "target"})
    skipless._children = [child]
    skipless.remove(child)
    assert skipless._children == []

    empty = DriveNode(drive, {"name": "p", "items": []})
    only_child = DriveNode(drive, {"docwsid": "c"})
    empty._children = [only_child]
    empty.remove(only_child)
    assert empty._children == []


def test_node_owner_record_name_variants() -> None:
    """Owner resolution only succeeds for addressable shared-folder nodes."""
    drive = _drive_service()
    assert DriveNode(drive, {"drivewsid": "FILE::x"}).owner_record_name is None
    assert (
        DriveNode(drive, {"drivewsid": "FILE_IN_SHARED_FOLDER::x"}).owner_record_name
        is None
    )
    assert (
        DriveNode(
            drive,
            {
                "drivewsid": "FILE_IN_SHARED_FOLDER::x",
                "shareID": "s",
                "owner": "not-a-dict",
            },
        ).owner_record_name
        is None
    )
    assert (
        DriveNode(
            drive,
            {
                "drivewsid": "FOLDER_IN_SHARED_FOLDER::x",
                "share": {"id": "s"},
                "owner": {"ownerRecordName": "Owner"},
            },
        ).owner_record_name
        == "Owner"
    )
    assert (
        DriveNode(
            drive,
            {
                "drivewsid": "FOLDER_IN_SHARED_FOLDER::x",
                "shareID": "s",
                "owner": {"ownerRecordName": 5},
            },
        ).owner_record_name
        is None
    )


def test_node_open_zero_size_returns_empty_response() -> None:
    """Zero-byte files short-circuit to an empty in-memory response."""
    drive = _drive_service()
    node = DriveNode(drive, {"size": 0})
    response = node.open()
    assert response.raw is not None
    assert response.raw.read() == b""


def test_node_open_shared_folder_passes_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening a shared-folder node forwards its owner record name."""
    drive = _drive_service()
    node = DriveNode(
        drive,
        {
            "size": "10",
            "docwsid": "doc",
            "zone": CLOUD_DOCS_ZONE,
            "drivewsid": "FILE_IN_SHARED_FOLDER::x",
            "shareID": "s",
            "owner": {"ownerRecordName": "Owner"},
        },
    )
    get_file = MagicMock(return_value=Mock(spec=Response))
    monkeypatch.setattr(drive, "get_file", get_file)

    node.open(stream=True)

    get_file.assert_called_once_with(
        "doc",
        zone=CLOUD_DOCS_ZONE,
        stream=True,
        owner_record_name="Owner",
    )


def test_node_operations_delegate_to_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload, mkdir, rename, trash and delete delegate to the service."""
    drive = _drive_service()
    node = DriveNode(
        drive,
        {
            "drivewsid": "node",
            "etag": "etag",
            "docwsid": "doc",
            "zone": CLOUD_DOCS_ZONE,
        },
    )
    send_file = MagicMock()
    create_folders = MagicMock(return_value="created")
    rename_items = MagicMock(return_value="renamed")
    move_items_to_trash = MagicMock(return_value="trashed")
    delete_items = MagicMock(return_value="deleted")
    monkeypatch.setattr(drive, "send_file", send_file)
    monkeypatch.setattr(drive, "create_folders", create_folders)
    monkeypatch.setattr(drive, "rename_items", rename_items)
    monkeypatch.setattr(drive, "move_items_to_trash", move_items_to_trash)
    monkeypatch.setattr(drive, "delete_items", delete_items)

    file_object = Mock()
    file_object.name = "f.txt"
    node.upload(file_object)
    send_file.assert_called_once_with("doc", file_object, zone=CLOUD_DOCS_ZONE)

    assert node.mkdir("new") == "created"
    create_folders.assert_called_once_with("node", "new")

    assert node.rename("new name") == "renamed"
    rename_items.assert_called_once_with("node", "etag", "new name")

    assert node.move_to_trash() == "trashed"
    move_items_to_trash.assert_called_once_with("node", "etag")

    assert node.delete() == "deleted"
    delete_items.assert_called_once_with("node", "etag")


def test_node_recover_and_delete_forever_require_restore_path() -> None:
    """Nodes outside the trash cannot be recovered or permanently deleted."""
    drive = _drive_service()
    node = DriveNode(drive, {"name": "orphan"})
    with pytest.raises(ValueError, match="does not appear to be in the Trash"):
        node.recover()
    with pytest.raises(ValueError, match="does not appear to be in the Trash"):
        node.delete_forever()


def test_node_get_on_file_raises_not_a_directory() -> None:
    """``get`` refuses to descend into a file node."""
    drive = _drive_service()
    node = DriveNode(drive, {"type": "file", "name": "f"})
    with pytest.raises(NotADirectoryError):
        node.get("child")


def test_node_str_and_repr() -> None:
    """The string and representation forms include type and name."""
    drive = _drive_service()
    node = DriveNode(drive, {"type": "file", "name": "f"})
    assert str(node) == "{type: file, name: f}"
    assert repr(node) == "<DriveNode: {type: file, name: f}>"


def test_event_fully_populated_skips_defaults() -> None:
    """Provided local dates, guid and tz bypass the ``__post_init__`` defaults."""
    event = EventObject(
        pguid="cal",
        start_date=datetime(2026, 1, 1, 10, 0),
        end_date=datetime(2026, 1, 1, 11, 0),
        local_start_date=datetime(2026, 1, 1, 9, 0),
        local_end_date=datetime(2026, 1, 1, 10, 0),
        guid="GUID-1",
        tz="Europe/Paris",
    )
    assert event.local_start_date == datetime(2026, 1, 1, 9, 0)
    assert event.local_end_date == datetime(2026, 1, 1, 10, 0)
    assert event.guid == "GUID-1"
    assert event.tz == "Europe/Paris"


def test_event_get_returns_fields_and_none() -> None:
    """``EventObject.get`` returns present fields and ``None`` otherwise."""
    event = EventObject(pguid="cal")
    assert event.get("title") == "New Event"
    assert event.get("does-not-exist") is None


def test_add_invitees_empty_is_noop() -> None:
    """Calling ``add_invitees`` with nothing leaves the list untouched."""
    event = EventObject(pguid="cal")
    event.add_invitees()
    assert not event.invitees


def test_calendar_object_keeps_supplied_guid_and_color() -> None:
    """A supplied guid and color are not regenerated."""
    calendar = CalendarObject(guid="GUID-2", color="#abcdef")
    assert calendar.guid == "GUID-2"
    assert calendar.color == "#abcdef"


def test_refresh_duration_skips_objects_without_duration() -> None:
    """Objects lacking a ``duration`` attribute are left untouched."""
    service = _calendar_service()
    target = SimpleNamespace(
        start_date=datetime(2026, 1, 1, 10, 0),
        end_date=datetime(2026, 1, 1, 11, 0),
    )
    service._refresh_duration(target)
    assert not hasattr(target, "duration")


def test_obj_from_dict_ignores_unknown_fields() -> None:
    """Keys that do not map to a dataclass field are skipped."""
    service = _calendar_service()
    event = service.obj_from_dict(
        EventObject(pguid="cal"), {"unknownKey": 1, "title": "T"}
    )
    assert event.title == "T"


def test_obj_from_dict_plain_object_sets_attributes() -> None:
    """Non-dataclass objects receive fields via ``setattr``."""
    service = _calendar_service()
    target = SimpleNamespace()
    service.obj_from_dict(target, {"alpha": 1, "beta": 2})
    assert target.alpha == 1
    assert target.beta == 2


def test_get_ctag_from_object_and_dict_and_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_ctag`` handles object and dict calendars, and raises otherwise."""
    service = _calendar_service()

    monkeypatch.setattr(
        service,
        "get_calendars",
        MagicMock(return_value=[CalendarObject(guid="g1", ctag="ctag1")]),
    )
    assert service.get_ctag("g1") == "ctag1"

    monkeypatch.setattr(
        service,
        "get_calendars",
        MagicMock(return_value=[{"guid": "g2", "ctag": "ctag2"}]),
    )
    assert service.get_ctag("g2") == "ctag2"

    monkeypatch.setattr(
        service,
        "get_calendars",
        MagicMock(return_value=[{"guid": "other"}]),
    )
    with pytest.raises(ValueError, match="ctag not found"):
        service.get_ctag("missing")


def test_refresh_client_respects_both_bounds() -> None:
    """When both bounds are given they are used verbatim."""
    mock_session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.json.return_value = {"Event": []}
    mock_session.get.return_value = response
    service = CalendarService("https://example.com", mock_session, {"dsid": "1"})

    service.refresh_client(from_dt=datetime(2025, 3, 10), to_dt=datetime(2025, 3, 20))

    params = mock_session.get.call_args.kwargs["params"]
    assert params["startDate"] == "2025-03-10"
    assert params["endDate"] == "2025-03-20"


def test_get_calendars_as_objects() -> None:
    """``get_calendars(as_objs=True)`` materialises calendar objects."""
    mock_session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.json.return_value = {"Collection": [{"title": "Work"}]}
    mock_session.get.return_value = response
    service = CalendarService("https://example.com", mock_session, {"dsid": "1"})

    calendars = service.get_calendars(as_objs=True)

    assert isinstance(calendars[0], CalendarObject)
    assert calendars[0].title == "Work"


def _events_service(events: list[dict[str, Any]]) -> tuple[CalendarService, MagicMock]:
    """Build a Calendar service whose refresh returns the given events."""
    mock_session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.json.return_value = {"Event": events}
    mock_session.get.return_value = response
    service = CalendarService("https://example.com", mock_session, {"dsid": "1"})
    return service, mock_session


def test_get_events_day_and_week_periods() -> None:
    """The day and week periods derive their own bounds."""
    service, _ = _events_service([])
    assert service.get_events(period="day", from_dt=datetime(2025, 3, 10)) == []
    assert service.get_events(period="day") == []
    assert service.get_events(period="week") == []
    assert service.get_events(period="week", from_dt=datetime(2025, 3, 10)) == []


def test_get_events_as_objects() -> None:
    """``as_objs=True`` maps events, raising when a pGuid is missing."""
    service, _ = _events_service([{"pGuid": "cal", "title": "T"}])
    events = service.get_events(as_objs=True)
    assert isinstance(events[0], EventObject)
    assert events[0].title == "T"

    service, _ = _events_service([{"title": "no-pguid"}])
    with pytest.raises(ValueError, match="missing required pGuid"):
        service.get_events(as_objs=True)


def test_get_event_detail_as_object() -> None:
    """``get_event_detail(as_obj=True)`` returns a mapped event."""
    mock_session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.json.return_value = {"Event": [{"pGuid": "cal", "title": "Detail"}]}
    mock_session.get.return_value = response
    service = CalendarService("https://example.com", mock_session, {"dsid": "1"})

    event = service.get_event_detail("cal", "guid", as_obj=True)

    assert isinstance(event, EventObject)
    assert event.title == "Detail"


def test_remove_event_uses_existing_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pre-existing etag is reused instead of refetching the event."""
    mock_session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.json.return_value = {"status": "success"}
    mock_session.post.return_value = response
    service = CalendarService("https://example.com", mock_session, {"dsid": "1"})
    monkeypatch.setattr(service, "get_ctag", MagicMock(return_value="ctag"))

    event = EventObject(pguid="cal")
    event.etag = "my-etag"

    assert service.remove_event(event) == {"status": "success"}
    assert mock_session.post.call_args.kwargs["params"]["ifMatch"] == "my-etag"
