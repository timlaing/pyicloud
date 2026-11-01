"""Upload credentials stay local while the existing wire requests are preserved."""

# pylint: disable=protected-access

from io import BytesIO
from typing import Any
from unittest.mock import Mock, patch

import pytest

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.services.base import BaseService
from pyicloud.services.drive import (
    CLOUD_DOCS_ZONE,
    COOKIE_APPLE_WEBAUTH_VALIDATE,
    DriveService,
)


class UploadFile(BytesIO):
    """In-memory upload with the file name expected by the Drive API."""

    name = "example.bin"


@pytest.mark.parametrize("failure_stage", [-1, 0, 1, 2])
@pytest.mark.parametrize("size", [0, 10])
def test_upload_parameters_are_operation_local(
    mock_service_with_cookies: PyiCloudService, failure_stage: int, size: int
) -> None:
    """Every upload stage preserves shared parameters on success and failure."""
    session = mock_service_with_cookies.session
    shared: dict[str, Any] = {"clientId": "client", "dsid": "123"}
    drive = DriveService(
        "https://drive.example.test", "https://docs.example.test", session, shared
    )
    other_service = BaseService("https://other.example.test", session, shared)
    original = dict(shared)
    expected_params = {**original, "token": "768y9u"}
    preparation_url = f"https://docs.example.test/ws/{CLOUD_DOCS_ZONE}/upload/web"
    finalisation_url = (
        f"https://docs.example.test/ws/{CLOUD_DOCS_ZONE}/update/documents"
    )
    content_url = "https://content.example.test/upload"
    file = UploadFile(b"x" * size)
    file.seek(size)
    file_info = {
        "fileChecksum": "sum",
        "wrappingKey": "key",
        "referenceChecksum": "ref",
        "size": size,
    }
    if size:
        file_info["receipt"] = "receipt"
    responses: list[Any] = [
        [{"document_id": "document", "url": content_url}],
        {"singleFile": file_info},
        {"status": "OK"},
    ]
    calls = 0

    def post(url: str, **_kwargs: Any) -> Mock:
        nonlocal calls
        stage = calls
        calls += 1
        if url == content_url:
            # Responses may refresh cookies; finalisation must keep this upload's
            # original parameters rather than pick up unrelated state changes.
            session.cookies.set(
                COOKIE_APPLE_WEBAUTH_VALIDATE,
                "t=rotated",
                domain="icloud.com",
                path="/",
            )
        return Mock(
            ok=stage != failure_stage,
            reason="Synthetic rejection",
            status_code=400,
            json=lambda: responses[stage],
        )

    with (
        patch.object(session, "post", side_effect=post) as transport,
        patch("mimetypes.guess_type", return_value=("application/octet-stream", None)),
    ):
        if failure_stage >= 0:
            with pytest.raises(
                PyiCloudAPIResponseException, match="Synthetic rejection"
            ):
                drive.send_file("folder", file, mtime=1, ctime=2)
        else:
            drive.send_file("folder", file, mtime=1, ctime=2)

    assert shared == original
    assert drive.params is shared
    assert other_service.params is shared
    assert other_service.params == original
    assert file.tell() == size
    assert calls == (failure_stage + 1 if failure_stage >= 0 else 3)
    preparation = transport.call_args_list[0]
    assert preparation.args == (preparation_url,)
    assert preparation.kwargs["params"] == expected_params
    assert preparation.kwargs["params"] is not shared
    assert preparation.kwargs["json"]["size"] == size
    if calls >= 2:
        content = transport.call_args_list[1]
        assert content.args == (content_url,)
        assert content.kwargs == {"files": {file.name: file}}
    if calls == 3:
        finalisation = transport.call_args_list[2]
        assert finalisation.args == (finalisation_url,)
        assert finalisation.kwargs["params"] == expected_params
        assert finalisation.kwargs["params"] is not shared
        payload = finalisation.kwargs["json"]
        assert payload["document_id"] == "document"
        assert payload["path"] == {"starting_document_id": "folder", "path": file.name}
        assert payload["mtime"] == 1000
        assert payload["btime"] == 2000
        assert payload["data"].get("receipt") == ("receipt" if size else None)


@pytest.mark.parametrize("reject", [False, True])
def test_preparation_alone_preserves_shared_parameters(
    mock_service_with_cookies: PyiCloudService, reject: bool
) -> None:
    """The preparation helper cannot leak its token, including on rejection."""
    drive = mock_service_with_cookies.drive
    original = dict(drive.params)
    response = Mock(ok=not reject, reason="Synthetic rejection", status_code=400)
    response.json.return_value = [
        {"document_id": "document", "url": "https://content.example.test/upload"}
    ]
    with (
        patch.object(drive.session, "post", return_value=response) as transport,
        patch("mimetypes.guess_type", return_value=("application/octet-stream", None)),
    ):
        if reject:
            with pytest.raises(
                PyiCloudAPIResponseException, match="Synthetic rejection"
            ):
                drive._get_upload_contentws_url(UploadFile())
        else:
            drive._get_upload_contentws_url(UploadFile())
    assert drive.params == original
    assert transport.call_args.kwargs["params"] == {**original, "token": "768y9u"}
    assert transport.call_args.kwargs["params"] is not drive.params
