"""Hide my email service."""

import json
from typing import Any, Generator, Optional

from requests import Response

from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession


class HideMyEmailService(BaseService):
    """
    The 'Hide My Email' iCloud service connects to iCloud and manages email aliases.

    This service allows users to:
    - Generate new email aliases
    - Reserve specific aliases
    - List all existing aliases
    - Get alias details by ID
    - Update alias metadata (label, note)
    - Delete aliases
    - Deactivate aliases
    - Reactivate aliases
    """

    def __init__(
        self, service_root: str, session: PyiCloudSession, params: dict[str, Any]
    ) -> None:
        super().__init__(service_root, session, params)

        # Define v1 endpoints
        self._v1_endpoint: str = f"{service_root}/v1/hme"
        self._generate_endpoint: str = f"{self._v1_endpoint}/generate"
        self._reserve_endpoint: str = f"{self._v1_endpoint}/reserve"
        self._update_metadata_endpoint: str = f"{self._v1_endpoint}/updateMetaData"
        self._delete_endpoint: str = f"{self._v1_endpoint}/delete"
        self._deactivate_endpoint: str = f"{self._v1_endpoint}/deactivate"
        self._reactivate_endpoint: str = f"{self._v1_endpoint}/reactivate"

        # Define v2 endpoints
        self._v2_endpoint: str = f"{service_root}/v2/hme"
        self._list_endpoint: str = f"{self._v2_endpoint}/list"
        self._get_endpoint: str = f"{self._v2_endpoint}/get"

    def generate(self) -> Optional[str]:
        """
        Generate a new email alias.

        Returns:
            The generated email address string or None if generation failed.
        """
        req: Response = self.session.post(self._generate_endpoint, params=self.params)
        response: dict[str, dict[str, str]] = req.json()
        result: Optional[dict[str, str]] = response.get("result")
        if result:
            return result.get("hme")
        return None

    def reserve(self, email: str, label: str, note="Generated") -> dict[str, Any]:
        """
        Reserve an alias for emails.

        Args:
            email: The email alias to reserve.
            label: A label for the email alias.
            note: An optional note for the email alias.

        Returns:
            The API's result containing details about the reserved alias.
        """
        req: Response = self.session.post(
            self._reserve_endpoint,
            params=self.params,
            data=json.dumps(
                {
                    "hme": email,
                    "label": label,
                    "note": note,
                }
            ),
        )
        response = req.json()
        return response.get("result", {})

    def __len__(self) -> int:
        """
        Get the number of emails
        """
        req: Response = self.session.get(self._list_endpoint, params=self.params)
        response: dict[str, dict[str, str]] = req.json()
        result: Optional[dict[str, str]] = response.get("result")
        if result:
            return len(result.get("hmeEmails", []))
        return 0

    def __iter__(self) -> Generator[Any, Any, None]:
        """
        Iterate over the list of emails
        """
        req: Response = self.session.get(self._list_endpoint, params=self.params)
        response: dict[str, dict[str, str]] = req.json()
        result: Optional[dict[str, str]] = response.get("result")
        if result:
            yield from result.get("hmeEmails", [])

    def __getitem__(self, anonymous_id: str) -> dict[str, Any]:
        """
        Get alias email details by anonymous_id.

        Args:
            anonymous_id: The unique identifier for the alias.

        Returns:
            A dictionary containing details about the alias.
        """
        req: Response = self.session.post(
            self._get_endpoint,
            params=self.params,
            data=json.dumps({"anonymousId": anonymous_id}),
        )
        response = req.json()
        return response.get("result", {})

    def update_metadata(
        self, anonymous_id: str, label: str, note: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Update metadata for an alias email.

        Args:
            anonymous_id: The unique identifier for the alias.
            label: The new label for the alias.
            note: The new note for the alias (optional).

        Returns:
            A dictionary containing the API response.
        """
        payload: dict[str, str] = {
            "anonymousId": anonymous_id,
            "label": label,
        }
        if note is not None:
            payload["note"] = note

        req: Response = self.session.post(
            self._update_metadata_endpoint,
            params=self.params,
            data=json.dumps(payload),
        )
        response = req.json()
        return response.get("result", {})

    def delete(self, anonymous_id: str) -> dict[str, Any]:
        """
        Delete an alias email.

        Args:
            anonymous_id: The unique identifier for the alias to delete.

        Returns:
            A dictionary containing the API response.
        """
        req: Response = self.session.post(
            self._delete_endpoint,
            params=self.params,
            data=json.dumps({"anonymousId": anonymous_id}),
        )
        response = req.json()
        return response.get("result", {})

    def deactivate(self, anonymous_id: str) -> dict[str, Any]:
        """
        Deactivate an alias email.

        Deactivating an alias means emails sent to it will no longer be forwarded,
        but the alias remains in your list and can be reactivated later.

        Args:
            anonymous_id: The unique identifier for the alias to deactivate.

        Returns:
            A dictionary containing the API response.
        """
        req: Response = self.session.post(
            self._deactivate_endpoint,
            params=self.params,
            data=json.dumps({"anonymousId": anonymous_id}),
        )
        response = req.json()
        return response.get("result", {})

    def reactivate(self, anonymous_id: str) -> dict[str, Any]:
        """
        Reactivate a previously deactivated alias email.

        Reactivating an alias means emails sent to it will be forwarded again
        to your primary inbox.

        Args:
            anonymous_id: The unique identifier for the alias to reactivate.

        Returns:
            A dictionary containing the API response.
        """
        req: Response = self.session.post(
            self._reactivate_endpoint,
            params=self.params,
            data=json.dumps({"anonymousId": anonymous_id}),
        )
        response = req.json()
        return response.get("result", {})
