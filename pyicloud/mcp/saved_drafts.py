"""Private immutable context for server-created, verified drafts only."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, cast

from pyicloud.services.mail.models import MailDraft


@dataclass(frozen=True, repr=False)
class SavedDraftContext:
    """Never reconstruct sender/recipient/body expectations from a message ID."""

    draft: MailDraft
    payload_json: str
    account_binding: str
    resource_binding: str
    revision: str

    @property
    def payload(self) -> dict[str, Any]:
        """Return a detached payload; callers cannot alter stored expectations."""
        return cast(dict[str, Any], json.loads(self.payload_json))
