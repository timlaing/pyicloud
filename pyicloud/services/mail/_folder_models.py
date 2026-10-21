"""Captured single-folder mutation requests and operation-specific outcomes."""

from typing import Annotated, Literal

from pydantic import Field

from ._mailboxes import MailboxSnapshot
from ._write_models import MutationResponse, WriteModel

FolderIntent = Literal["gemsCreateMailbox", "renameMailboxGems", "deleteMailboxGems"]


class FolderRequest(WriteModel):
    """Never expose arbitrary domains or bulk folder operations."""

    domain: Literal["mailbox"] = "mailbox"


class FolderName(WriteModel):
    """A validated full wire path, not a filesystem path."""

    name: Annotated[str, Field(strict=True, min_length=1, max_length=512)]


class FolderCreateRequest(FolderRequest):
    """The stable caller key is mailbox, not a captured identifier."""

    create: dict[Literal["mailbox"], FolderName] = Field(min_length=1, max_length=1)


class FolderRenameRequest(FolderRequest):
    """An exact folder ID and replacement server path."""

    update: dict[str, FolderName] = Field(min_length=1, max_length=1)


class FolderDeleteRequest(FolderRequest):
    """One confirmed folder identifier, never a wildcard or bulk selection."""

    destroy: tuple[str]


class CreatedFolder(MailboxSnapshot):
    """Creation includes an additional ID which must match identifier."""

    created_id: Annotated[str, Field(strict=True, min_length=1)] = Field(alias="id")
    is_deleted: bool = Field(strict=True, alias="isMboxDeleted")


class FolderResponse(MutationResponse[None, CreatedFolder]):
    """Folder rename values are null, not message-update acknowledgement objects."""
