"""Offline boundary regressions for Photos review findings; no live writes."""

# pylint: disable=protected-access

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from requests import Session
from requests.adapters import HTTPAdapter

from pyicloud.common.cloudkit import CKRecord
from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.content import DownloadState
from pyicloud.mcp.context import BoundedAdapter, ToolFailure
from pyicloud.mcp.upload_transport import UploadTransport
from pyicloud.services.photos.client import PhotosCloudKitClient
from pyicloud.services.photos.exceptions import PhotosApiError
from pyicloud.services.photos.service import PhotoLibrary
from tests.test_mcp_content import content_for
from tests.test_mcp_photo_album_creates import setup_create
from tests.test_mcp_photo_album_writes import make_album, setup_album
from tests.test_mcp_photo_relations import setup_add
from tests.test_mcp_photo_writes import setup_photos


@pytest.mark.asyncio
@pytest.mark.parametrize("create", [False, True])
async def test_surrogate_album_names_are_safe_denials(create: bool) -> None:
    """Unpaired surrogates cannot escape the invalid-payload contract."""
    if create:
        creator, _client, _library, handle = setup_create()
        operation = creator.prepare_create(handle, {"name": "\ud800"})
        ledger = creator.ledger
    else:
        writer, _client, _album, handle = setup_album()
        operation = writer.prepare(handle, {"name": "\ud800"})
        ledger = writer.ledger
    with pytest.raises(WriteDenied, match="invalid_payload"):
        await operation
    assert not ledger._pending


@pytest.mark.asyncio
async def test_nested_inventory_does_not_expand_rename_eligibility() -> None:
    """Nested albums are inventory records, not top-level rename targets."""
    writer, _client, library, handle = setup_create()
    album = make_album(library, parent="Folder")
    library.server_albums.return_value = [album]
    preview = await writer.prepare_create(handle, {"name": "Unrelated"})
    assert preview.payload == {"name": "Unrelated"}
    library.server_albums.assert_called_once_with(max_records=999)
    with pytest.raises(WriteDenied, match="unsupported_target"):
        writer._selector(album)


@pytest.mark.asyncio
@pytest.mark.parametrize("applying", [False, True])
async def test_inventory_failure_is_sanitized_before_consumption(
    applying: bool,
) -> None:
    """Bounded producer errors never escape as raw Photos errors or spend approval."""
    writer, _client, library, handle = setup_create()
    data = {"name": "New"}
    preview = None
    if applying:
        preview = await writer.prepare_create(handle, data)
        writer.ledger.approve_from_operator(preview.request_id, preview.digest)
    library.server_albums.side_effect = PhotosApiError("private")
    operation = (
        writer.apply(preview.request_id)
        if preview
        else writer.prepare_create(handle, data)
    )
    with pytest.raises(WriteDenied, match="inventory_unavailable") as raised:
        await operation
    assert str(raised.value) == "inventory_unavailable"
    if preview:
        assert preview.request_id in writer.ledger._pending
    else:
        assert not writer.ledger._pending


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", [" ", "null", " NULL "])
async def test_invalid_fresh_revisions_have_no_approval(tag: str) -> None:
    """Fresh album and asset revisions must be usable exact change tags."""
    albums, _client, album, handle = setup_album()
    album._record_change_tag = tag
    with pytest.raises(WriteDenied, match="revision_unavailable"):
        await albums.prepare(handle, {"name": "After"})
    photos, _client, asset, handle = setup_photos()
    asset.asset_record.recordChangeTag = tag
    with pytest.raises(WriteDenied, match="revision_unavailable"):
        await photos.prepare(handle, {"favorite": True})


@pytest.mark.asyncio
async def test_source_foreign_zone_is_rejected_before_fresh_lookup() -> None:
    """Record names cannot rebind a foreign discovered asset to private data."""
    writer, _client, album, asset, source, target = setup_add()
    asset.asset_record.zoneID.zoneName = "Foreign"
    album._library.all.get.reset_mock()
    with pytest.raises(WriteDenied, match="relation_unavailable"):
        await writer.prepare_add(source, target, {})
    album._library.all.get.assert_not_called()


def test_bounded_inventory_stops_before_fetching_more_pages() -> None:
    """The producer reads at most the bound plus one overflow sentinel."""
    library = MagicMock(spec=PhotoLibrary)
    library.service = SimpleNamespace(session=SimpleNamespace(post=MagicMock()))
    library.zone_id = {"zoneName": "Z"}
    record = CKRecord.model_validate({
        "recordName": "A",
        "recordType": "CPLAlbum",
        "fields": {},
    })
    query = MagicMock(
        return_value=SimpleNamespace(
            records=[record, record], continuationMarker="more"
        )
    )
    library._client = SimpleNamespace(query=query)
    library._bounded_album_pages.side_effect = lambda client, parent, remaining: (
        PhotoLibrary._bounded_album_pages(library, client, parent, remaining)
    )
    with (
        patch(
            "pyicloud.services.photos.service._can_use_typed_cloudkit",
            return_value=True,
        ),
        pytest.raises(PhotosApiError, match="inventory limit"),
    ):
        PhotoLibrary._bounded_album_records(library, 1)
    query.assert_called_once()
    assert query.call_args.kwargs["results_limit"] == 2


def test_bounded_inventory_accepts_complete_pages_and_rejects_repeated_cursor() -> None:
    """Accepted inventory reads all pages; a broken cursor cannot loop forever."""
    library = MagicMock(spec=PhotoLibrary)
    library.zone_id = {"zoneName": "Z"}
    record = CKRecord.model_validate({
        "recordName": "A",
        "recordType": "CPLAlbum",
        "fields": {},
    })
    query = MagicMock(
        side_effect=[
            SimpleNamespace(records=[record], continuationMarker="next"),
            SimpleNamespace(records=[], continuationMarker=None),
        ]
    )
    client = MagicMock(spec=PhotosCloudKitClient)
    client.query = query
    assert list(PhotoLibrary._bounded_album_pages(library, client, None, 1)) == [record]
    assert query.call_count == 2
    query.side_effect = None
    query.return_value = SimpleNamespace(records=[], continuationMarker="repeat")
    broken = PhotoLibrary._bounded_album_pages(library, client, None, 1)
    with pytest.raises(PhotosApiError, match="Repeated album inventory cursor"):
        list(broken)


def test_asset_resource_binding_is_independent_of_zone_field_order() -> None:
    """Equivalent extra zone fields cannot spuriously retire an exact approval."""
    writer, client, asset, _handle = setup_photos()
    client.photos.private_primary_zone = {"zoneName": "Z", "extra": {"a": 1, "b": 2}}
    target = writer._selector(asset)
    first = writer._fresh(client, target)[2]
    client.photos.private_primary_zone = {"extra": {"b": 2, "a": 1}, "zoneName": "Z"}
    assert writer._fresh(client, target)[2] == first


def test_download_capacity_preserves_active_entity_tags() -> None:
    """Failed version admission can replace only confirmed inactive state."""
    tools = content_for(MagicMock())
    active = DownloadState(object(), etag='"v1"', active=True)
    tools._downloads = {str(index): active for index in range(1000)}
    with pytest.raises(ToolFailure, match="download_registry_full"):
        tools._download_state("new", object())
    tools._downloads["999"] = DownloadState(object())
    tools._download_state("new", object())
    assert tools._downloads["0"] is active
    assert active.etag == '"v1"'


@pytest.mark.parametrize("supported", [False, True])
def test_registration_deadline_is_enforced_or_refused_before_dispatch(
    supported: bool,
) -> None:
    """Custom sessions cannot dispatch registration without deadline-aware transport."""
    session = cast(Any, Session())
    session.trust_env = False
    session.request_raw = MagicMock()
    adapter = BoundedAdapter(30) if supported else HTTPAdapter(max_retries=0)
    session.get_adapter = MagicMock(return_value=adapter)
    hosts = frozenset({"service.example.test"})
    with patch("pyicloud.mcp.upload_transport.time.monotonic", return_value=1):
        if supported:
            assert isinstance(adapter, BoundedAdapter)
            with (
                patch(
                    "pyicloud.mcp.upload_transport.bounded_upload_json", return_value={}
                ),
                patch(
                    "pyicloud.mcp.upload_transport.request_once", return_value=object()
                ),
            ):
                UploadTransport.register(
                    session, "https://service.example.test/a", hosts, {}, deadline=5
                )
            assert adapter.deadline is None
        else:
            with pytest.raises(WriteDenied, match="upload_deadline_policy_unavailable"):
                UploadTransport.register(
                    session, "https://service.example.test/a", hosts, {}, deadline=5
                )
            session.request_raw.assert_not_called()


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("prior", [None, 3.0, 30.0])
def test_registration_deadline_is_temporary_on_all_exit_paths(
    fail: bool, prior: float | None
) -> None:
    """A short registration deadline cannot poison subsequent verification reads."""
    adapter = BoundedAdapter(30)
    adapter.deadline = prior
    session = cast(Any, Session())
    session.trust_env = False
    session.get_adapter = MagicMock(return_value=adapter)

    def request(*_args: object, **_kwargs: object) -> object:
        assert adapter.deadline == (min(prior, 5) if prior is not None else 5)
        if fail:
            raise RuntimeError("private")
        return object()

    hosts = frozenset({"service.example.test"})
    with (
        patch("pyicloud.mcp.upload_transport.time.monotonic", return_value=1),
        patch("pyicloud.mcp.upload_transport.bounded_upload_json", return_value={}),
        patch("pyicloud.mcp.upload_transport.request_once", side_effect=request),
    ):
        if fail:
            with pytest.raises(WriteDenied, match="upload_outcome_uncertain"):
                UploadTransport.register(
                    session, "https://service.example.test/a", hosts, {}, deadline=5
                )
        else:
            UploadTransport.register(
                session, "https://service.example.test/a", hosts, {}, deadline=5
            )
    assert adapter.deadline == prior


def test_abandoned_download_expires_without_evicting_recent_active_state() -> None:
    """Only aged or inactive downloads can free a slot for new admission."""
    tools = content_for(MagicMock())
    active = DownloadState(object(), etag='"live"', active=True, touched=1000)
    abandoned = DownloadState(object(), etag='"old"', active=True, touched=399)
    tools._downloads = {str(index): active for index in range(1000)}
    tools._downloads["999"] = abandoned
    with patch("pyicloud.mcp.content.time.monotonic", return_value=1000):
        tools._download_state("new", object())
    assert tools._downloads["0"] is active
    assert "999" not in tools._downloads


def test_relation_resource_binding_is_stable_for_reordered_zone_fields() -> None:
    """Equivalent zone dictionaries cannot change relation approval identity."""
    writer, client, album, asset, _source, _target = setup_add()
    target = (album.id, asset.id, asset.master_id, writer._selector(album)[1])
    with (
        patch.object(album, "contains_photo_scoped", return_value=False),
        patch("pyicloud.mcp.photo_relations.record_zone") as zone,
    ):
        zone.return_value = {"zoneName": "Z", "extra": {"a": 1, "b": 2}}
        first = writer._pair(client, target)[3]
        zone.return_value = {"extra": {"b": 2, "a": 1}, "zoneName": "Z"}
        assert writer._pair(client, target)[3] == first
