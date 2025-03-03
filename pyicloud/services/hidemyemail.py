"""Hide my email service."""

import json

from .base import BaseService


class HideMyEmailService(BaseService):
    """
    The 'Hide My Email' iCloud service, connects to iCloud and create alias emails.
    """

    def __init__(self, service_root, session, params) -> None:
        super().__init__(service_root, session, params)
        hme_endpoint: str = f"{service_root}/v1/hme"
        self._hidemyemail_generate: str = f"{hme_endpoint}/generate"
        self._hidemyemail_reserve: str = f"{hme_endpoint}/reserve"

        self.response = {}

    def generate(self):
        """
        Generate alias for the emails
        """
        req = self.session.post(self._hidemyemail_generate, params=self.params)
        self.response = req.json()
        return self.response.get("result").get("hme")

    def reserve(self, email, label, note="Generated") -> None:
        """
        Reserve alias for the emails
        """
        data: str = json.dumps({"hme": email, "label": label, "note": note})

        self.session.post(self._hidemyemail_reserve, params=self.params, data=data)
