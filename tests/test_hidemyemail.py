"""Tests for the Hide My Email service."""

import json
from typing import Any, Optional
from unittest.mock import MagicMock

from requests import Response

from pyicloud.services.hidemyemail import HideMyEmailService


def test_generate(
    hidemyemail_service: HideMyEmailService, mock_session: MagicMock
) -> None:
    """Test the generate method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"hme": "alias@example.com"}}
    mock_session.post.return_value = mock_response

    result: Optional[str] = hidemyemail_service.generate()
    assert result == "alias@example.com"
    mock_session.post.assert_called_once_with(
        "https://example.com/v1/hme/generate", params={"dsid": "12345"}
    )


def test_reserve(
    hidemyemail_service: HideMyEmailService, mock_session: MagicMock
) -> None:
    """Test the reserve method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"status": "success"}}
    mock_session.post.return_value = mock_response

    result: dict[str, Any] = hidemyemail_service.reserve(
        "alias@example.com", "Test Label", "Test Note"
    )
    assert result == {"status": "success"}
    mock_session.post.assert_called_once_with(
        "https://example.com/v1/hme/reserve",
        params={"dsid": "12345"},
        data=json.dumps(
            {"hme": "alias@example.com", "label": "Test Label", "note": "Test Note"}
        ),
    )


def test_len(hidemyemail_service: HideMyEmailService, mock_session: MagicMock) -> None:
    """Test the __len__ method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"hmeEmails": ["email1", "email2"]}}
    mock_session.get.return_value = mock_response

    result: int = len(hidemyemail_service)
    assert result == 2
    mock_session.get.assert_called_once_with(
        "https://example.com/v2/hme/list", params={"dsid": "12345"}
    )


def test_iter(hidemyemail_service: HideMyEmailService, mock_session: MagicMock) -> None:
    """Test the __iter__ method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"hmeEmails": ["email1", "email2"]}}
    mock_session.get.return_value = mock_response

    emails = list(iter(hidemyemail_service))
    assert emails == ["email1", "email2"]
    mock_session.get.assert_called_once_with(
        "https://example.com/v2/hme/list", params={"dsid": "12345"}
    )


def test_getitem(
    hidemyemail_service: HideMyEmailService, mock_session: MagicMock
) -> None:
    """Test the __getitem__ method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"email": "alias@example.com"}}
    mock_session.post.return_value = mock_response

    result: dict[str, Any] = hidemyemail_service["12345"]
    assert result == {"email": "alias@example.com"}
    mock_session.post.assert_called_once_with(
        "https://example.com/v2/hme/get",
        params={"dsid": "12345"},
        data=json.dumps({"anonymousId": "12345"}),
    )


def test_update_metadata(
    hidemyemail_service: HideMyEmailService, mock_session: MagicMock
) -> None:
    """Test the update_metadata method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"status": "updated"}}
    mock_session.post.return_value = mock_response

    result: dict[str, Any] = hidemyemail_service.update_metadata(
        "12345", "New Label", "New Note"
    )
    assert result == {"status": "updated"}
    mock_session.post.assert_called_once_with(
        "https://example.com/v1/hme/updateMetaData",
        params={"dsid": "12345"},
        data=json.dumps(
            {"anonymousId": "12345", "label": "New Label", "note": "New Note"}
        ),
    )


def test_delete(
    hidemyemail_service: HideMyEmailService, mock_session: MagicMock
) -> None:
    """Test the delete method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"status": "deleted"}}
    mock_session.post.return_value = mock_response

    result: dict[str, Any] = hidemyemail_service.delete("12345")
    assert result == {"status": "deleted"}
    mock_session.post.assert_called_once_with(
        "https://example.com/v1/hme/delete",
        params={"dsid": "12345"},
        data=json.dumps({"anonymousId": "12345"}),
    )


def test_deactivate(
    hidemyemail_service: HideMyEmailService, mock_session: MagicMock
) -> None:
    """Test the deactivate method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"status": "deactivated"}}
    mock_session.post.return_value = mock_response

    result: dict[str, Any] = hidemyemail_service.deactivate("12345")
    assert result == {"status": "deactivated"}
    mock_session.post.assert_called_once_with(
        "https://example.com/v1/hme/deactivate",
        params={"dsid": "12345"},
        data=json.dumps({"anonymousId": "12345"}),
    )


def test_reactivate(
    hidemyemail_service: HideMyEmailService, mock_session: MagicMock
) -> None:
    """Test the reactivate method."""
    mock_response = MagicMock(spec=Response)
    mock_response.json.return_value = {"result": {"status": "reactivated"}}
    mock_session.post.return_value = mock_response

    result: dict[str, Any] = hidemyemail_service.reactivate("12345")
    assert result == {"status": "reactivated"}
    mock_session.post.assert_called_once_with(
        "https://example.com/v1/hme/reactivate",
        params={"dsid": "12345"},
        data=json.dumps({"anonymousId": "12345"}),
    )
