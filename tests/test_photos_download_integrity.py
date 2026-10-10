"""Asset downloads validate decoded bodies and release their responses."""

# pylint: disable=protected-access,super-init-not-called

import gzip
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from urllib3.response import HTTPResponse

from pyicloud.services.photos_cloudkit.client import PhotosCloudKitClient
from pyicloud.services.photos_cloudkit.models import (
    PhotoResource,
    PhotosServiceException,
)
from pyicloud.services.photos_cloudkit.service import PhotoAsset


def asset(size: int | None = 6) -> tuple[Any, MagicMock]:
    """Asset."""
    response = MagicMock()
    service = MagicMock()
    service.session.get.return_value = response
    subject = object.__new__(PhotoAsset)
    subject._service = service
    subject._resources = {
        "original": PhotoResource(
            key="original",
            filename="asset.jpg",
            url="https://icloud.com/asset",
            size=size,
            type="public.jpeg",
        )
    }
    return subject, response


@pytest.mark.parametrize("chunks", [[b"abc"], [b"abcd", b"more"]])
def test_wrong_size_is_rejected_and_response_closed(chunks: list[bytes]) -> None:
    """Wrong size is rejected and response closed."""
    subject, response = asset()
    response.iter_content.return_value = iter(chunks)
    with pytest.raises(PhotosServiceException, match="size differs"):
        subject.download()
    response.close.assert_called_once()


@pytest.mark.parametrize("size", [None, 0, 6])
def test_decoded_chunks_are_returned_and_response_closed(size: int | None) -> None:
    """Decoded chunks are returned and response closed."""
    subject, response = asset(size)
    response.iter_content.return_value = iter([b"abc", b"", b"def"])
    assert subject.download() == b"abcdef"
    response.raw.read.assert_not_called()
    response.close.assert_called_once()


def test_stream_failure_closes_response() -> None:
    """Stream failure closes response."""
    subject, response = asset()
    response.iter_content.side_effect = OSError("interrupted")
    with pytest.raises(OSError, match="interrupted"):
        subject.download()
    response.close.assert_called_once()


def test_status_failure_closes_response() -> None:
    """Status failure closes response."""
    subject, response = asset()
    response.raise_for_status.side_effect = OSError("status failure")
    with pytest.raises(OSError, match="status failure"):
        subject.download()
    response.close.assert_called_once()


def test_typed_client_stream_is_closed_on_oversize() -> None:
    """Typed client stream is closed on oversize."""
    subject, _ = asset()
    closed: list[bool] = []

    def chunks() -> Any:
        """Chunks."""
        try:
            yield b"too long"
            pytest.fail("oversized stream must stop immediately")
        finally:
            closed.append(True)

    client = object.__new__(PhotosCloudKitClient)
    client._client = MagicMock()
    client._client.download_asset_stream.return_value = chunks()
    subject._service.private_client = client
    with (
        patch(
            "pyicloud.services.photos_cloudkit.service._can_use_typed_cloudkit",
            return_value=True,
        ),
        pytest.raises(PhotosServiceException, match="size differs"),
    ):
        subject.download()
    assert closed == [True]


def test_missing_resource_does_not_request() -> None:
    """Missing resource does not request."""
    subject, _ = asset()
    assert subject.download("missing") is None
    subject._service.session.get.assert_not_called()


def test_compressed_response_returns_decoded_asset_bytes() -> None:
    """Compressed response returns decoded asset bytes."""
    subject, _ = asset()
    response = requests.Response()
    response.status_code = 200
    response.raw = HTTPResponse(
        body=BytesIO(gzip.compress(b"abcdef")),
        headers={"Content-Encoding": "gzip"},
        preload_content=False,
    )
    subject._service.session.get.return_value = response
    assert subject.download() == b"abcdef"
    assert response.raw.closed
