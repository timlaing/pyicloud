"""Synthetic startup/staging/gateway guards for elevated Photos uploads."""

import base64
import hashlib
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.context import ServerConfig
from pyicloud.mcp.write_gateway import WriteGateway
from tests.test_mcp_photo_uploads import setup_upload


@pytest.mark.parametrize(
    "hosts",
    [
        (),
        ("https://host.test",),
        ("127.0.0.1",),
        ("UPPER.test",),
        ("bad-.test",),
        ("local",),
        tuple(f"a{i}.test" for i in range(9)),
    ],
)
def test_upload_host_policy_is_independent_startup_selection(
    hosts: tuple[str, ...],
) -> None:
    """No URL schemes, local IPs, implicit host trust or unbounded policy."""
    with pytest.raises(ValueError):
        ServerConfig(
            username="synthetic",
            session_dir="unused",
            services=("photos",),
            write_actions=("photos.upload_file",),
            high_risk_actions=("photos.upload_file",),
            approval_dir="unused",
            upload_transfer_hosts=hosts,
        )


@pytest.mark.asyncio
async def test_staging_and_upload_gateway_are_receipt_gated() -> None:
    """Local byte stages grant no remote approval and consumed bytes cannot replay."""
    adapter, client, _library, handle, _token, pipeline = setup_upload()
    owner = cast(Any, adapter.additional.reads.owner)
    owner.config = ServerConfig(
        username="synthetic",
        session_dir="unused",
        services=("photos",),
        write_actions=("photos.upload_file",),
        high_risk_actions=("photos.upload_file",),
        approval_dir="unused",
        upload_transfer_hosts=("content.example.test",),
    )
    mailbox = MagicMock()
    with patch("pyicloud.mcp.write_gateway.ApprovalMailbox", return_value=mailbox):
        gateway = WriteGateway(adapter.additional.reads, adapter.additional)
    assert gateway.photo_uploads is not None
    gateway.photo_uploads.pipeline = pipeline
    data = b"approved"
    begun = await gateway.stage(
        "begin",
        {
            "name": "photo.jpg",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    assert begun.result is not None
    token = begun.result["stage_handle"]
    assert (await gateway.stage("describe", {"stage_handle": token})).error is None
    assert (
        await gateway.stage(
            "append",
            {
                "stage_handle": token,
                "offset": 0,
                "base64": base64.b64encode(data).decode(),
            },
        )
    ).error is None
    assert (await gateway.stage("seal", {"stage_handle": token})).error is None
    assert (await gateway.stage("seal", {"path": "private"})).error == "invalid_payload"
    assert (
        await gateway.prepare("photos.upload_file", "", {"confirm": True}, None, handle)
    ).error == "invalid_payload"
    prepared = await gateway.prepare(
        "photos.upload_file",
        "",
        {"stage_handle": token, "modified_ms": 1000},
        None,
        handle,
    )
    assert prepared.preview is not None
    preview = prepared.preview

    def ordinary(identifier: str, ledger: Any) -> None:
        ledger.approve_from_operator(identifier, preview["digest"])

    mailbox.accept.side_effect = ordinary
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "high_risk_operator_approval_required"
    pipeline.upload.assert_not_called()
    mailbox.accept.side_effect = lambda identifier, ledger: (
        ledger.approve_from_operator(identifier, preview["digest"], high_risk=True)
    )
    pipeline.upload.side_effect = OSError("private")
    assert (await gateway.apply(preview["request_id"])).error == "mutation_uncertain"
    pipeline.upload.assert_called_once()
    assert (
        await gateway.apply(preview["request_id"])
    ).error == "unknown_or_consumed_preview"
    assert (await gateway.stage("describe", {"stage_handle": token})).error is not None
    second = await gateway.stage(
        "begin",
        {"name": "discard.jpg", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()},
    )
    assert second.result is not None
    assert (
        await gateway.stage("discard", {"stage_handle": second.result["stage_handle"]})
    ).result == {"discarded": True}
    client.photos.session = object()
