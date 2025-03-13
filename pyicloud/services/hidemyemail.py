"""Hide my email service."""

import json
from typing import Any, Optional

from requests import Response

from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession


class HideMyEmailService(BaseService):
    """
    The 'Hide My Email' iCloud service, connects to iCloud and create alias emails.
    """

    def __init__(
        self, service_root: str, session: PyiCloudSession, params: dict[str, Any]
    ) -> None:
        super().__init__(service_root, session, params)
        hme_endpoint: str = f"{service_root}/v1/hme"
        self._hidemyemail_generate: str = f"{hme_endpoint}/generate"
        self._hidemyemail_reserve: str = f"{hme_endpoint}/reserve"

    def generate(self) -> Optional[str]:
        """
        Generate alias for the emails
        """
        req: Response = self.session.post(
            self._hidemyemail_generate, params=self.params
        )
        response: dict[str, dict[str, str]] = req.json()
        result: Optional[dict[str, str]] = response.get("result")
        if result:
            return result.get("hme")

    def reserve(self, email: str, label: str, note="Generated") -> None:
        """
        Reserve alias for the emails
        """
        data: str = json.dumps({"hme": email, "label": label, "note": note})

        self.session.post(self._hidemyemail_reserve, params=self.params, data=data)
