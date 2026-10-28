"""Observed single-message permanent destruction, not a Trash move or purge."""

from typing import Annotated, Literal

from pydantic import Field

from ._write_models import MutationResponse, WriteModel, known_rejection
from .exceptions import MailWriteRejected, MailWriteUncertain

DELETE_UNCERTAIN = (
    "Permanent Mail deletion outcome is uncertain; the selected message may "
    "already have been deleted. Inspect Trash before any explicit retry. "
    "No automatic retry or cleanup was performed."
)


class MessageDeleteRequest(WriteModel):
    """A tuple of exactly one identifier cannot become an empty-Trash request."""

    domain: Literal["email"] = "email"
    destroy: tuple[Annotated[str, Field(min_length=1)]]


class MessageDeleteResponse(MutationResponse[None, None]):
    """Require the complete captured mutation envelope and exact destroyed IDs."""


def deletion_acknowledged(
    response: MessageDeleteResponse, account_id: str, message_id: str
) -> None:
    """Conflicting, unrelated or missing outcomes are never safely retryable."""
    if response.account_id != account_id or any((
        response.created,
        response.updated,
        response.not_created,
        response.not_updated,
    )):
        raise MailWriteUncertain(DELETE_UNCERTAIN)
    if response.not_destroyed:
        if not response.destroyed and known_rejection(
            response.not_destroyed, message_id
        ):
            raise MailWriteRejected("Mail rejected permanent deletion; refresh Trash.")
        raise MailWriteUncertain(DELETE_UNCERTAIN)
    if response.destroyed != [message_id]:
        raise MailWriteUncertain(DELETE_UNCERTAIN)
