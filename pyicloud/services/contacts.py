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

        self._contacts: Optional[list] = None

    def refresh_client(self) -> None:
        """
        Refreshes the ContactsService endpoint, ensuring that the
        contacts data is up-to-date.
        """
        params_contacts = dict(self.params)
        params_contacts.update(
            {
                "clientVersion": "2.1",
                "locale": "en_US",
                "order": "last,first",
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
