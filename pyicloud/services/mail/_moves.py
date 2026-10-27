"""Mailbox-only move shapes; no implicit flag changes or permanent deletion."""

from typing import Annotated, Literal

from pydantic import Field

from ._write_models import UpdateResponse, WriteModel
from .models import MailModel


class MailboxReplacement(WriteModel):
    """One explicit destination, never a guessed folder role."""

    replace: tuple[str]


class MovePatch(WriteModel):
    """Only change mailbox membership, preserving all message flags."""

    mailbox: MailboxReplacement = Field(alias="mboxRef")


class MoveOperation(WriteModel):
    """Move one exact source identifier."""

    ids: tuple[str]
    patch: MovePatch


class MoveRequest(WriteModel):
    """The captured ordinary email/set move request."""

    domain: Literal["email"] = "email"
    batch_update: tuple[MoveOperation] = Field(alias="batchUpdate")


class MoveAcknowledgement(WriteModel):
    """The captured empty acknowledgement does not identify the destination copy."""


class MoveResponse(UpdateResponse[MoveAcknowledgement]):
    """Moves do not return the counter required by flag acknowledgements."""


class MoveFolder(MailModel):
    """Captured mailbox query properties, not mailbox/set-only boolean fields."""

    id: Annotated[str, Field(strict=True, min_length=1)] = Field(alias="identifier")
    uid_validity: Annotated[int, Field(strict=True, gt=0)] = Field(alias="uidValidity")
    name: Annotated[str, Field(strict=True, min_length=1)]
    flags: Annotated[int, Field(strict=True, ge=0)]


class MoveFolderResponse(MailModel):
    """An exact mailbox capability lookup, not a partial folder listing."""

    records: list[MoveFolder] = Field(alias="domainObjects")
    continuation_marker: str | None = Field(default=None, alias="continuationMarker")
