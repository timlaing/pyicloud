"""Unit tests for the ContactsService and MeCard classes."""
# pylint: disable=protected-access

from unittest.mock import MagicMock, patch

import pytest

from pyicloud.services.contacts import ContactsService, MeCard


def test_contacts_service_initialization(contacts_service: ContactsService) -> None:
    """Test the initialization of ContactsService."""
    assert contacts_service._contacts_endpoint == "https://example.com/co"
    assert contacts_service._contacts_refresh_url == "https://example.com/co/startup"
    assert contacts_service._contacts_next_url == "https://example.com/co/contacts"
    assert (
        contacts_service._contacts_changeset_url == "https://example.com/co/changeset"
    )
    assert contacts_service._contacts_me_card_url == "https://example.com/co/mecard"
    assert contacts_service._contacts is None


@patch("requests.Response")
def test_refresh_client(
    mock_response, contacts_service: ContactsService, mock_session: MagicMock
) -> None:
    """Test the refresh_client method."""
    mock_response.json.return_value = {
        "prefToken": "test_pref_token",
        "syncToken": "test_sync_token",
        "contacts": [{"firstName": "John", "lastName": "Doe"}],
    }
    mock_session.get.return_value = mock_response

    contacts_service.refresh_client()

    mock_session.get.assert_called()
    assert contacts_service._contacts == [
        {
            "firstName": "John",
            "lastName": "Doe",
        }
    ]


@patch("requests.Response")
def test_all_property(
    mock_response, contacts_service: ContactsService, mock_session: MagicMock
) -> None:
    """Test the all property."""
    mock_response.json.return_value = {
        "prefToken": "test_pref_token",
        "syncToken": "test_sync_token",
        "contacts": [{"firstName": "John", "lastName": "Doe"}],
    }
    mock_session.get.return_value = mock_response

    contacts = contacts_service.all

    mock_session.get.assert_called()
    assert contacts == [{"firstName": "John", "lastName": "Doe"}]


@patch("requests.Response")
def test_me_property(
    mock_response, contacts_service: ContactsService, mock_session: MagicMock
) -> None:
    """Test the me property."""
    mock_response.json.return_value = {
        "contacts": [{"firstName": "Jane", "lastName": "Smith", "photo": "photo_url"}]
    }
    mock_session.get.return_value = mock_response

    me_card: MeCard = contacts_service.me

    mock_session.get.assert_called()
    assert isinstance(me_card, MeCard)
    assert me_card.first_name == "Jane"
    assert me_card.last_name == "Smith"
    assert me_card.photo == "photo_url"


def test_me_card_initialization() -> None:
    """Test the initialization of MeCard."""
    data: dict[str, list[dict[str, str]]] = {
        "contacts": [
            {"firstName": "Alice", "lastName": "Johnson", "photo": "photo_url"}
        ]
    }
    me_card = MeCard(data)

    assert me_card.first_name == "Alice"
    assert me_card.last_name == "Johnson"
    assert me_card.photo == "photo_url"
    assert me_card.raw_data == data


def test_me_card_invalid_data() -> None:
    """Test MeCard initialization with invalid data."""
    with pytest.raises(KeyError):
        MeCard({"invalid_key": "value"})
