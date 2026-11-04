"""Synthetic Drive byte transport and elevated acknowledged-only upload adapter."""

from dataclasses import replace
import hashlib
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.drive_upload_bytes import DriveUploadBytes
from pyicloud.mcp.drive_uploads import DriveUploads
from pyicloud.mcp.upload_staging import StagedUpload
from tests.test_mcp_upload_previews import setup_upload


@pytest.mark.parametrize(
    "case",
    [
        "ok",
        "empty",
        "reservation",
        "receipt",
        "commit",
        "transport",
        "expired",
        "credentials",
        "root",
        "query",
        "name",
        "allocation",
        "receipt_token",
    ],
)
def test_existing_upload_contract_never_retries_or_claims_final_identity(
    case: str,
) -> None:
    """Exact existing allocation/multipart/commit fields and operation-local token."""
    transport, drive = MagicMock(), MagicMock()
    drive.params = {"dsid": "123"}
    drive.document_root = "https://docs.example.test"
    if case == "root":
        drive.document_root = None
    if case == "query":
        drive.document_root += "?bad=1"
    drive._get_token_from_cookie.return_value = {"token": "synthetic"}  # pylint: disable=protected-access
    data = b"" if case == "empty" else b"approved"
    stage = StagedUpload(
        "file.txt",
        len(data),
        hashlib.sha256(data).hexdigest(),
        time.monotonic() + 300,
        data,
    )
    if case == "expired":
        stage = replace(stage, expires_at=0)
    if case == "name":
        stage = replace(stage, name="../file")
    if case == "credentials":
        drive._get_token_from_cookie.return_value = {}  # pylint: disable=protected-access
    transport.register.side_effect = [
        []
        if case == "reservation"
        else [
            {"document_id": "allocated", "url": "https://content.example.test/upload"}
        ],
        {"status": "NO" if case == "commit" else "OK"},
    ]
    if case == "allocation":
        transport.register.side_effect = [
            [{"document_id": None, "url": "https://content.example.test/upload"}]
        ]
    receipt = {
        "size": len(data),
        "fileChecksum": "sum",
        "referenceChecksum": "ref",
        "wrappingKey": "key",
    }
    if data:
        receipt["receipt"] = "synthetic"
    if case == "receipt":
        receipt["size"] = 99
    if case == "receipt_token":
        receipt.pop("receipt")
    transport.transfer.return_value = {"singleFile": receipt}
    if case == "transport":
        transport.transfer.side_effect = OSError("private")
    uploader = DriveUploadBytes(transport)
    with patch(
        "pyicloud.mcp.drive_upload_bytes.mimetypes.guess_type",
        return_value=("text/plain", None),
    ):
        if case in {"ok", "empty"}:
            uploader.upload(drive, "folder-document", stage, modified_ms=1000)
            commit = transport.register.call_args_list[1]
            assert "token=synthetic" in commit.args[1]
            assert commit.args[3]["document_id"] == "allocated"
            assert commit.args[3]["path"] == {
                "starting_document_id": "folder-document",
                "path": "file.txt",
            }
            assert commit.args[3]["mtime"] == commit.args[3]["btime"] == 1000
            assert commit.args[3]["allow_conflict"] is True
            assert transport.transfer.call_args.kwargs["multipart_name"] == "file.txt"
        else:
            with pytest.raises(WriteDenied):
                uploader.upload(drive, "folder-document", stage, modified_ms=1000)
    assert drive.params == {"dsid": "123"}
    assert transport.register.call_count <= 2
    assert transport.transfer.call_count <= 1


def setup_adapter() -> tuple[DriveUploads, Any, str, str, dict[str, Any], MagicMock]:
    """Shared staged bytes and private destination, with fake upload dispatch."""
    writer, client, handle, token, data = setup_upload()
    data["docwsid"] = "folder-document"
    client.drive.params = dict(client.params)
    pipeline = MagicMock()
    return (
        DriveUploads(
            writer, writer.staging, frozenset({"content.example.test"}), pipeline
        ),
        client,
        handle,
        token,
        data,
        pipeline,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "ok",
        "transport",
        "route",
        "account",
        "stale",
        "conflict",
        "missing",
        "hosts",
        "timestamp",
    ],
)
async def test_elevated_upload_binding_retirement_and_acknowledgement(
    case: str,
) -> None:
    """No transfer without exact elevated approval; no stage/target/request replay."""
    writes, client, handle, token, data, pipeline = setup_adapter()
    if case == "hosts":
        writes.hosts = frozenset()
    if case == "timestamp":
        with pytest.raises(WriteDenied):
            await writes.prepare_upload(handle, token, True)
        return
    if case == "missing":
        data.pop("docwsid")
    if case == "conflict":
        data["items"] = [{"name": "New", "extension": "txt", "type": "FILE"}]
    if case in {"hosts", "missing", "conflict"}:
        with pytest.raises(WriteDenied):
            await writes.prepare_upload(handle, token, 1000)
        pipeline.upload.assert_not_called()
        return
    preview = await writes.prepare_upload(handle, token, 1000)
    assert preview.payload["allow_conflict"] is True
    assert preview.risk == "high"
    with pytest.raises(WriteDenied):
        await writes.apply(preview.request_id)
    pipeline.upload.assert_not_called()
    writes.ledger.approve_from_operator(
        preview.request_id, preview.digest, high_risk=True
    )
    if case == "stale":
        data["etag"] = "changed"
        with pytest.raises(WriteDenied):
            await writes.apply(preview.request_id)
        pipeline.upload.assert_not_called()
        return

    def dispatch(*_args: Any, **_kwargs: Any) -> None:
        with pytest.raises(WriteDenied):
            writes.staging.peek(token)
        if case == "transport":
            raise OSError("private")
        if case == "route":
            client.drive.document_root = "https://other.test"
        if case == "account":
            client.params["dsid"] = "456"

    pipeline.upload.side_effect = dispatch
    if case == "ok":
        assert await writes.apply(preview.request_id) == {
            "action": "drive.upload_file",
            "acknowledged": True,
            "final_state_verified": False,
        }
    else:
        with pytest.raises(WriteDenied, match="mutation_uncertain"):
            await writes.apply(preview.request_id)
    pipeline.upload.assert_called_once()
    with pytest.raises(WriteDenied, match="unknown_or_consumed"):
        await writes.apply(preview.request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "service",
        "query",
        "root",
        "params",
        "target",
        "capacity",
        "risk",
        "consumed_risk",
    ],
)
async def test_preview_policy_and_route_guards(case: str) -> None:
    """Unsafe policy/roots fail before bytes retire; registry is bounded."""
    writes, client, handle, token, _data, pipeline = setup_adapter()
    if case == "service":
        writes.additional.reads.owner.config = replace(
            writes.additional.reads.owner.config, services=()
        )
    elif case == "query":
        client.drive.service_root += "?bad=1"
    elif case == "root":
        client.drive.document_root = None
    elif case == "params":
        client.drive.params["dsid"] = "other"
    elif case == "target":
        handle = writes.additional.remember("drive-folder", object())
    elif case == "capacity":
        writes._requests.update({  # pylint: disable=protected-access
            str(i): (handle, MagicMock(), token) for i in range(256)
        })  # pylint: disable=protected-access
        publish = MagicMock()
        preview = await writes.prepare_upload(handle, token, 1000, publish)
        publish.assert_called_once_with(preview)
        assert len(writes._requests) == 256  # pylint: disable=protected-access
        return
    if case == "risk":
        original = writes.ledger.preview
        with (
            patch.object(
                writes.ledger,
                "preview",
                side_effect=lambda *a, **k: replace(original(*a, **k), risk="standard"),
            ),
            pytest.raises(WriteDenied),
        ):
            await writes.prepare_upload(handle, token, 1000)
    elif case == "consumed_risk":
        preview = await writes.prepare_upload(handle, token, 1000)
        writes.ledger.approve_from_operator(
            preview.request_id, preview.digest, high_risk=True
        )
        consume = writes.ledger.consume
        with (
            patch.object(
                writes.ledger,
                "consume",
                side_effect=lambda *a, **k: replace(consume(*a, **k), risk="standard"),
            ),
            pytest.raises(WriteDenied),
        ):
            await writes.apply(preview.request_id)
        writes.staging.peek(token)
    else:
        with pytest.raises(WriteDenied):
            await writes.prepare_upload(handle, token, 1000)
    pipeline.upload.assert_not_called()
