"""Existing Photos wire fixtures exercised using synthetic immutable bytes only."""

from dataclasses import replace
import hashlib
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.photo_upload_bytes import PhotoUploadBytes
from pyicloud.mcp.upload_staging import StagedUpload
from tests.fixture_loader import load_fixture

UUID = "11111111-2222-3333-4444-555555555555"


def setup_upload() -> tuple[PhotoUploadBytes, MagicMock, StagedUpload]:
    """Reuse protocol envelopes, not a newly invented success response."""
    data = b"x" * 3105
    stage = StagedUpload(
        "new_upload.jpg",
        len(data),
        hashlib.sha256(data).hexdigest(),
        time.monotonic() + 300,
        data,
    )
    transport = MagicMock()
    transport.register.side_effect = [
        load_fixture("photos_upload", "create_upload_url_response.json"),
        load_fixture("photos_upload", "put_asset_response.json"),
    ]
    transport.transfer.return_value = load_fixture(
        "photos_upload", "single_file_upload_response.json"
    )
    return PhotoUploadBytes(transport), transport, stage


@pytest.mark.parametrize(
    "case",
    ["ok", "reservation", "receipt", "results", "identity", "status", "transport"],
)
def test_exact_path_free_flow_and_no_retry(case: str) -> None:
    """Exact wire sizes/names/receipts, with ambiguity never resubmitted."""
    uploader, transport, stage = setup_upload()
    if case == "reservation":
        transport.register.side_effect = [{"uploadUrls": {}}]
    elif case == "receipt":
        transport.transfer.return_value = {"singleFile": {"size": 1}}
    elif case in {"results", "identity", "status"}:
        result = load_fixture("photos_upload", "put_asset_response.json")
        if case == "results":
            result = []
        elif case == "identity":
            result[0]["cplAsset"] = None
        else:
            result[0]["response"]["status"] = 500
        transport.register.side_effect = [
            load_fixture("photos_upload", "create_upload_url_response.json"),
            result,
        ]
    elif case == "transport":
        transport.transfer.side_effect = OSError("private")
    with patch("pyicloud.mcp.photo_upload_bytes.uuid4", return_value=UUID):
        args = (
            MagicMock(),
            "https://photosupload.example.test",
            {"dsid": "12345"},
            "PrimarySync",
            stage,
        )
        if case == "ok":
            result = uploader.upload(*args, modified_ms=1787731877546)
            assert (
                result.cplAsset
                == load_fixture("photos_upload", "put_asset_response.json")[0][
                    "cplAsset"
                ]
            )
            first = transport.register.call_args_list[0].args
            assert first[3] == {"zoneName": "PrimarySync", "assets": {UUID: 3105}}
            final = transport.register.call_args_list[1].args[3]
            assert final["files"][0]["fileName"] == stage.name
            assert (
                final["files"][0]["singleFileUploadRequest"]
                == transport.transfer.return_value["singleFile"]
            )
            assert final["localTimeZoneId"] == "UTC"
        else:
            with pytest.raises(WriteDenied, match="upload_outcome_uncertain"):
                uploader.upload(*args, modified_ms=1)
    assert transport.register.call_count <= 2
    assert transport.transfer.call_count <= 1


@pytest.mark.parametrize(
    "case", ["digest", "expired", "path", "size", "account", "zone", "date", "query"]
)
def test_invalid_context_never_reserves(case: str) -> None:
    """Private-only route, valid sealed bytes and metadata required before dispatch."""
    uploader, transport, stage = setup_upload()
    updates: dict[str, dict[str, Any]] = {
        "digest": {"sha256": "bad"},
        "expired": {"expires_at": 0},
        "path": {"name": "../file"},
        "size": {"size": 0},
    }
    stage = replace(stage, **updates.get(case, {}))
    session = MagicMock()
    with pytest.raises(WriteDenied):
        uploader.upload(
            session,
            "https://photosupload.example.test" + ("?bad=1" if case == "query" else ""),
            {"dsid": "bad" if case == "account" else "12345"},
            "SharedSync" if case == "zone" else "PrimarySync",
            stage,
            modified_ms=True if case == "date" else 1,
        )
    transport.register.assert_not_called()
    transport.transfer.assert_not_called()
