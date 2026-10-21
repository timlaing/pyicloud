"""Sanitized wire contracts for the ordinary draft-attachment implementation."""
# cspell:words cachedpart

from typing import Any

from pydantic import ValidationError
import pytest

from pyicloud.services.mail._draft_models import (
    DraftAttachmentReference,
    DraftSaveResponse,
    DraftUploadResponse,
)
from tests.fixture_loader import load_fixture


def test_modern_upload_fixture_round_trip() -> None:
    """Retain the modern response shape without leaking metadata through repr."""
    payload = load_fixture("mail", "draft_attachment_upload_response.json")
    response = DraftUploadResponse.model_validate(payload)
    assert response.model_dump(by_alias=True) == payload
    assert response.message_part.size == 20
    assert response.message_part.encoding == "base64"
    assert "synthetic.txt" not in repr(response)
    assert "cachedpart:" not in repr(response.message_part)


def test_attachment_save_reference_fixture_round_trip() -> None:
    """Null expiration and the false Mail Drop flag are actual wire fields."""
    payload = load_fixture("mail", "draft_attachment_reference.json")
    reference = DraftAttachmentReference.model_validate(payload)
    assert reference.model_dump(by_alias=True) == payload
    assert reference.content_id == reference.guid
    assert "synthetic.txt" not in repr(reference)
    assert "gateway.example.test" not in repr(reference)


@pytest.mark.parametrize(
    "key,value",
    [
        ("uid", "101"),
        ("uid", None),
        ("messagePart", None),
        ("unexpected", True),
    ],
)
def test_upload_envelope_rejects_unobserved_shapes(key: str, value: Any) -> None:
    """Do not confuse draft UIDs or extra outcomes with upload success."""
    payload = load_fixture("mail", "draft_attachment_upload_response.json")
    payload[key] = value
    with pytest.raises(ValidationError):
        DraftUploadResponse.model_validate(payload)


def test_upload_rejects_legacy_envelope() -> None:
    """The legacy msgPart validator must not stand in for modern messagePart."""
    payload = load_fixture("mail", "draft_attachment_upload_response.json")
    payload["msgPart"] = payload.pop("messagePart")
    with pytest.raises(ValidationError):
        DraftUploadResponse.model_validate(payload)


@pytest.mark.parametrize(
    "key,value",
    [
        ("guid", "messagepart:Drafts/100-1"),
        ("guid", "cachedpart:not-a-uuid"),
        ("size", -1),
        ("size", True),
        ("size", "20"),
        ("name", ""),
        ("url", ""),
        ("datatype", ""),
        ("encoding", "binary"),
        ("remote", True),
    ],
)
def test_uploaded_part_is_strict(key: str, value: Any) -> None:
    """Untrusted JSON cannot coerce sizes or introduce remote-upload capabilities."""
    payload = load_fixture("mail", "draft_attachment_upload_response.json")
    payload["messagePart"][key] = value
    with pytest.raises(ValidationError):
        DraftUploadResponse.model_validate(payload)


@pytest.mark.parametrize(
    "key,value",
    [
        ("size", False),
        ("size", -1),
        ("contentID", "messagepart:Drafts/100-1"),
        ("mailDropExpirationDate", "2030-01-01"),
        ("isMailDropThumbnail", True),
        ("isMailDropThumbnail", 0),
        ("name", ""),
        ("unknown", "value"),
    ],
)
def test_reference_rejects_unsupported_shapes(key: str, value: Any) -> None:
    """Ordinary cached references do not silently enable Mail Drop or loose types."""
    payload = load_fixture("mail", "draft_attachment_reference.json")
    payload[key] = value
    with pytest.raises(ValidationError):
        DraftAttachmentReference.model_validate(payload)


def test_save_response_retains_attachment_identity_mapping() -> None:
    """Successful saves can remap cached IDs; neither UID nor mappings may be lost."""
    payload = load_fixture("mail", "draft_attachment_save_response.json")
    response = DraftSaveResponse.model_validate(payload)
    assert response.uid == "102"
    assert response.attachments is not None
    mapping = response.attachments[0]
    assert mapping.request_guid == mapping.new_guid
    assert mapping.content_id is None
    assert response.model_dump(by_alias=True, exclude_none=True) == payload
    assert "cachedpart:" not in repr(response)


@pytest.mark.parametrize("key", ["requestGuid", "newGuid", "contentId"])
def test_save_response_rejects_empty_remapping_identifiers(key: str) -> None:
    """Malformed remapping data must not be interpreted as a reusable part handle."""
    payload = load_fixture("mail", "draft_attachment_save_response.json")
    payload["attachments"][0][key] = ""
    with pytest.raises(ValidationError):
        DraftSaveResponse.model_validate(payload)
