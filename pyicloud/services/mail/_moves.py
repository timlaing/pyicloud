"""Mailbox-only move shapes; no implicit flag changes or permanent deletion."""

from typing import Literal

from pydantic import Field

from ._write_models import UpdateResponse, WriteModel


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
