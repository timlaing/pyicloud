"""Fresh mailbox namespace and flag metadata used by explicit writes."""

from typing import Annotated

from pydantic import Field

from .models import MailModel


class MailboxSnapshot(MailModel):
    """Captured query properties, not mailbox/set-only boolean fields."""

    id: Annotated[str, Field(strict=True, min_length=1)] = Field(alias="identifier")
    uid_validity: Annotated[int, Field(strict=True, gt=0)] = Field(alias="uidValidity")
    name: Annotated[str, Field(strict=True, min_length=1)]
    flags: Annotated[int, Field(strict=True, ge=0)]


class MailboxSnapshotResponse(MailModel):
    """A capability query that must not silently accept partial results."""

    records: list[MailboxSnapshot] = Field(alias="domainObjects")
    continuation_marker: str | None = Field(default=None, alias="continuationMarker")
