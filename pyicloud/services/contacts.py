"""Contacts service."""

from typing import Any, Optional

from requests import Response

from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession


class ContactsService(BaseService):
    """
    The 'Contacts' iCloud service, connects to iCloud and returns contacts.
    """

    def __init__(
        self, service_root: str, session: PyiCloudSession, params: dict[str, Any]
    ) -> None:
        super().__init__(service_root, session, params)
        self._contacts_endpoint: str = f"{self.service_root}/co"
        self._contacts_refresh_url: str = f"{self._contacts_endpoint}/startup"
        self._contacts_next_url: str = f"{self._contacts_endpoint}/contacts"
        self._contacts_changeset_url: str = f"{self._contacts_endpoint}/changeset"
        self._contacts_me_card_url: str = f"{self._contacts_endpoint}/mecard"

        self._contacts: Optional[list] = None

    def refresh_client(self) -> None:
        """
        Refreshes the ContactsService endpoint, ensuring that the
        contacts data is up-to-date.
        """
        params_contacts = dict(self.params)
        params_contacts.update(
            {
                "locale": "en_US",
                "order": "last,first",
                "includePhoneNumbers": True,
                "includePhotos": True,
            }
        )
        req: Response = self.session.get(
            self._contacts_refresh_url, params=params_contacts
        )
        response: dict[str, Any] = req.json()

        params_next = dict(params_contacts)
        params_next.update(
            {
                "prefToken": response["prefToken"],
                "syncToken": response["syncToken"],
                "limit": "0",
                "offset": "0",
            }
        )
        req = self.session.get(self._contacts_next_url, params=params_next)
        response = req.json()
        self._contacts = response.get("contacts")

    @property
    def all(self):
        """
        Retrieves all contacts.
        """
        self.refresh_client()
        return self._contacts

    @property
    def me(self) -> "MeCard":
        """
        Retrieves the user's own contact information.
        """
        params_contacts = dict(self.params)
        req: Response = self.session.get(
            self._contacts_me_card_url, params=params_contacts
        )
        response = req.json()
        return MeCard(response)


class MeCard:
    """
    The 'MeCard' class represents the user's own contact information.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data: dict[str, Any] = data
        contacts = data.get("contacts")
        if isinstance(contacts, list) and isinstance(contacts[0], dict):
            self._contact: dict[str, Any] = contacts[0]
        else:
            raise KeyError("contacts not found in data")

    @property
    def first_name(self) -> str:
        """
        The user's first name.
        """
        return self._contact["firstName"]

    @property
    def last_name(self) -> str:
        """
        The user's last name.
        """
        return self._contact["lastName"]

    @property
    def photo(self):
        """
        The user's photo.
        """
        return self._contact["photo"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __repr__(self) -> str:
        return f"<MeCard({self.first_name}-{self.last_name})>"

    @property
    def raw_data(self) -> dict[str, Any]:
        """
        The raw data of the mecard.
        """
        return self._data
