"""Elevated synthetic uploads with exact bytes and independent indexed identity."""

import base64
from dataclasses import replace
import hashlib
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.common.cloudkit import CKRecord
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied
from pyicloud.mcp.photo_uploads import PhotoUploads
from pyicloud.mcp.upload_staging import UploadStaging
from pyicloud.services.photos.models import PhotosPutAssetResult
from pyicloud.services.photos.service import PhotoAsset, PhotosService
from tests.test_mcp_photo_album_creates import setup_create


def setup_upload() -> tuple[PhotoUploads, Any, Any, str, str, MagicMock]:
    """Only fake library and pipeline operations; no actual transport."""
    writer, client, library, handle = setup_create()
    library.zone_id = {"zoneName": "PrimarySync"}
    client.photos.params = {"dsid": "123"}
    client.photos.upload_root = "https://upload.example.test"
    writer.ledger = ApprovalLedger(
        frozenset({"photos.upload_file"}),
        high_risk_actions=frozenset({"photos.upload_file"}),
    )
    cast(Any, writer.additional.reads.owner).add_cleanup = MagicMock()
    data = b"approved bytes"
    stages = UploadStaging()
    token = stages.begin("photo.jpg", len(data), hashlib.sha256(data).hexdigest())
    stages.append(token, 0, base64.b64encode(data).decode())
    stages.seal(token)
    pipeline = MagicMock()
    pipeline.upload.return_value = PhotosPutAssetResult(cplMaster="M", cplAsset="P")
    upload = PhotoUploads(writer, stages, frozenset({"content.example.test"}), pipeline)
    return upload, client, library, handle, token, pipeline


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["ok", "transport", "absent", "wrong_master", "route", "account", "tag"]
)
async def test_elevated_exact_once_upload_and_independent_proof(case: str) -> None:
    """Retire bytes before dispatch and never replay absent or incorrect indexing."""
    upload, client, library, handle, token, pipeline = setup_upload()
    publish = MagicMock()
    preview = await upload.prepare_upload(handle, token, 1000, publish)
    publish.assert_called_once_with(preview)
    assert preview.risk == "high" and preview.payload["size"] == 14
    with pytest.raises(WriteDenied):
        await upload.apply(preview.request_id)
    pipeline.upload.assert_not_called()

    master = CKRecord.model_validate({
        "recordName": "OTHER" if case == "wrong_master" else "M",
        "recordType": "CPLMaster",
        "fields": {},
    })
    asset = CKRecord.model_validate({
        "recordName": "P",
        "recordType": "CPLAsset",
        "recordChangeTag": None if case == "tag" else "R",
        "zoneID": library.zone_id,
        "fields": {},
    })
    observed = PhotoAsset(client.photos, master, asset)

    def dispatch(*_args: Any, **_kwargs: Any) -> Any:
        with pytest.raises(WriteDenied):
            upload.staging.peek(token)
        if case == "transport":
            raise OSError("private")
        if case == "route":
            client.photos.upload_root = "https://other.test"
        if case == "account":
            client.params["dsid"] = "456"
        return pipeline.upload.return_value

    pipeline.upload.side_effect = dispatch
    with patch.object(
        type(library),
        "all",
        new_callable=lambda: property(
            lambda _self: MagicMock(
                get=MagicMock(return_value=None if case == "absent" else observed)
            )
        ),
    ):

        def accept(identifier: str) -> None:
            upload.ledger.approve_from_operator(
                identifier, preview.digest, high_risk=True
            )

        if case == "ok":
            assert await upload.apply(preview.request_id, accept) == {
                "action": "photos.upload_file",
                "id": "P",
                "duplicate": False,
                "verified": True,
            }
        else:
            with pytest.raises(WriteDenied, match="mutation_uncertain"):
                await upload.apply(preview.request_id, accept)
    pipeline.upload.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await upload.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["hosts", "shared", "root", "service", "timestamp", "target", "stage", "params"],
)
async def test_unsupported_context_never_dispatches(case: str) -> None:
    """Unsafe library/staging/route selections cannot reserve upload resources."""
    upload, client, library, handle, token, pipeline = setup_upload()
    if case == "hosts":
        upload.hosts = frozenset()
    elif case == "shared":
        library.zone_id = {"zoneName": "SharedSync"}
    elif case == "root":
        client.photos.upload_root = None
    elif case == "service":
        upload.additional.reads.owner.config = replace(
            upload.additional.reads.owner.config, services=()
        )
    elif case == "target":
        handle = upload.additional.remember("photo-library", object())
    elif case == "stage":
        upload.staging.discard(token)
    elif case == "params":
        client.photos.params["dsid"] = "456"
    with pytest.raises(WriteDenied):
        await upload.prepare_upload(
            handle, token, True if case == "timestamp" else 1000
        )
    pipeline.upload.assert_not_called()


def test_observed_photos_upload_root_is_read_only() -> None:
    """Accessing routing metadata does not discover libraries or mutate anything."""
    service = PhotosService(
        "https://photos.test",
        MagicMock(),
        {"dsid": "123"},
        None,
        None,
        photos_upload_url="https://upload.test",
    )
    assert service.upload_root == "https://upload.test"
    with pytest.raises(AttributeError):
        object.__setattr__(service, "upload_root", "https://other.test")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["policy", "capacity", "stale"])
async def test_preview_policy_capacity_and_revalidation(case: str) -> None:
    """High risk cannot downgrade and route changes invalidate approval."""
    upload, client, _library, handle, token, pipeline = setup_upload()
    if case == "capacity":
        upload._requests.update({str(i): ("zone", token, 1) for i in range(256)})  # pylint: disable=protected-access
    original = upload.ledger.preview
    if case == "policy":
        with (
            patch.object(
                upload.ledger,
                "preview",
                side_effect=lambda *a, **k: replace(original(*a, **k), risk="standard"),
            ),
            pytest.raises(WriteDenied, match="high_risk"),
        ):
            await upload.prepare_upload(handle, token, 1)
    else:
        preview = await upload.prepare_upload(handle, token, 1)
        if case == "capacity":
            assert len(upload._requests) == 256  # pylint: disable=protected-access
        else:
            upload.ledger.approve_from_operator(
                preview.request_id, preview.digest, high_risk=True
            )
            client.photos.upload_root = "https://other.test"
            with pytest.raises(WriteDenied):
                await upload.apply(preview.request_id)
    pipeline.upload.assert_not_called()


@pytest.mark.asyncio
async def test_consumed_approval_must_still_be_elevated() -> None:
    """Corrupted policy results cannot consume byte staging into a remote dispatch."""
    upload, _client, _library, handle, token, pipeline = setup_upload()
    preview = await upload.prepare_upload(handle, token, 1)
    upload.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    original = upload.ledger.consume
    with (
        patch.object(
            upload.ledger,
            "consume",
            side_effect=lambda *a, **k: replace(original(*a, **k), risk="standard"),
        ),
        pytest.raises(WriteDenied, match="high_risk"),
    ):
        await upload.apply(preview.request_id)
    upload.staging.peek(token)
    pipeline.upload.assert_not_called()
