"""Folder operations with fresh namespace checks and non-replayed submissions."""

from collections.abc import Callable
from dataclasses import dataclass

from requests import RequestException

from pyicloud.exceptions import PyiCloudException

from ._folder_models import (
    FolderCreateRequest,
    FolderDeleteRequest,
    FolderIntent,
    FolderName,
    FolderRenameRequest,
    FolderRequest,
    FolderResponse,
)
from ._folder_names import SYSTEM_FOLDERS, folder_path
from ._mailboxes import MailboxSnapshot
from ._write_models import known_rejection
from .exceptions import MailError, MailWriteRejected, MailWriteUncertain
from .models import MailFolder, MailFolderDeletion

FOLDER_UNCERTAIN = (
    "Mail folder mutation outcome is uncertain; refresh the folder inventory "
    "before retrying. No automatic retry or cleanup was performed."
)


@dataclass(frozen=True)
class FolderWriter:
    """Operation logic receives bounded reads and one non-replayed write path."""

    account_id: str
    inventory: Callable[[], list[MailboxSnapshot]]
    submit: Callable[[FolderRequest, FolderIntent], FolderResponse]
    has_messages: Callable[[MailFolder], bool]

    def create(self, name: str, parent: MailFolder | None) -> MailFolder:
        """Create one new leaf without replacing a pre-existing mailbox."""
        path = folder_path(name, "" if parent is None else self._context(parent).name)
        before = self.inventory()
        if parent is not None:
            self._current(parent, before, protect_system=False)
        self._collision(path, before)
        response = self.submit(
            FolderCreateRequest(create={"mailbox": FolderName(name=path)}),
            "gemsCreateMailbox",
        )
        self._outcome(response, "create", "mailbox")
        if response.created is None or set(response.created) != {"mailbox"}:
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        created = response.created["mailbox"]
        if (
            created.id != created.created_id
            or created.name != path
            or created.flags != 0
            or created.is_deleted
            or any(folder.id == created.id for folder in before)
        ):
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        return self._after(created.id, path, created.uid_validity)

    def rename(self, folder: MailFolder, name: str) -> MailFolder:
        """Rename a leaf within its existing parent; never move a folder tree."""
        original = self._context(folder)
        path = folder_path(name, original.name.rpartition("/")[0])
        before = self.inventory()
        current = self._current(original, before)
        self._no_children(current, before)
        if path == current.name:
            raise ValueError("The requested folder name is unchanged")
        self._collision(path, before)
        response = self.submit(
            FolderRenameRequest(update={current.id: FolderName(name=path)}),
            "renameMailboxGems",
        )
        self._outcome(response, "rename", current.id)
        if response.updated is None or set(response.updated) != {current.id}:
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        return self._after(current.id, path, current.uid_validity)

    def delete(
        self, folder: MailFolder, *, confirm: bool, allow_nonempty: bool
    ) -> MailFolderDeletion:
        """Permanently delete one explicitly confirmed custom folder."""
        if confirm is not True:
            raise ValueError(
                "Folder deletion requires confirm=True; "
                "it is irreversible and preflight is not atomic"
            )
        if not isinstance(allow_nonempty, bool):
            raise ValueError("allow_nonempty must be a boolean")
        original = self._context(folder)
        before = self.inventory()
        current = self._current(original, before)
        self._no_children(current, before)
        bound = self._bound(current)
        if self.has_messages(bound) and not allow_nonempty:
            raise MailError(
                "Folder contains messages; deletion requires allow_nonempty=True"
            )
        response = self.submit(
            FolderDeleteRequest(destroy=(current.id,)), "deleteMailboxGems"
        )
        self._outcome(response, "delete", current.id)
        if response.destroyed != [current.id]:
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        after = self._read_after()
        if any(item.id == current.id for item in after):
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        return MailFolderDeletion(folder_id=current.id)

    def _outcome(self, response: FolderResponse, operation: str, key: str) -> None:
        """Only a single operation's exact outcome can establish success/rejection."""
        if response.account_id != self.account_id:
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        success = {
            "create": response.created,
            "rename": response.updated,
            "delete": response.destroyed,
        }
        failure = {
            "create": response.not_created,
            "rename": response.not_updated,
            "delete": response.not_destroyed,
        }
        if any(value for kind, value in success.items() if kind != operation):
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        if any(value for kind, value in failure.items() if kind != operation):
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        failed = failure[operation]
        if failed:
            if not success[operation] and known_rejection(failed, key):
                raise MailWriteRejected(
                    "Mail rejected the folder mutation; refresh folders."
                )
            raise MailWriteUncertain(FOLDER_UNCERTAIN)

    @staticmethod
    def _no_children(current: MailboxSnapshot, records: list[MailboxSnapshot]) -> None:
        """Do not implicitly rename or destroy a descendant mailbox tree."""
        prefix = current.name.casefold() + "/"
        if any(other.name.casefold().startswith(prefix) for other in records):
            raise MailError(
                "Folder has child folders; implicit tree mutations are unsupported"
            )

    @staticmethod
    def _context(folder: MailFolder) -> MailFolder:
        """Reject guessed, malformed, or unbound namespaces before reads/writes."""
        if not isinstance(folder, MailFolder):
            raise ValueError("A discovered MailFolder is required")
        value = folder.uid_validity
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("A positive folder UID validity is required")
        return folder

    @staticmethod
    def _collision(path: str, records: list[MailboxSnapshot]) -> None:
        """Reject case aliases instead of relying on overwrite behavior."""
        if any(record.name.casefold() == path.casefold() for record in records):
            raise MailError(
                "A folder with that name already exists; no mutation submitted"
            )

    @staticmethod
    def _current(
        folder: MailFolder,
        records: list[MailboxSnapshot],
        *,
        protect_system: bool = True,
    ) -> MailboxSnapshot:
        """A saved name/ID/UID validity cannot silently target a new mailbox."""
        matches = [record for record in records if record.id == folder.id]
        if len(matches) != 1:
            raise MailError("The exact folder no longer exists; refresh folders")
        current = matches[0]
        if current.uid_validity != folder.uid_validity or current.name != folder.name:
            raise MailError("Folder identity changed; refresh folders before writing")
        if current.flags != 0 or current.name.startswith(("$category$_", "/")):
            raise MailError(
                "Folder mutations do not support labels or virtual mailboxes"
            )
        if protect_system and current.name.casefold() in SYSTEM_FOLDERS:
            raise MailError("System mailboxes cannot be renamed or deleted")
        return current

    def _read_after(self) -> list[MailboxSnapshot]:
        """A failed post-write read must not be mistaken for a safe retry condition."""
        try:
            return self.inventory()
        except (PyiCloudException, RequestException, OSError) as exc:
            raise MailWriteUncertain(FOLDER_UNCERTAIN) from exc

    def _after(self, identifier: str, name: str, validity: int) -> MailFolder:
        """Return fresh metadata only after an exact post-write namespace match."""
        matches = [record for record in self._read_after() if record.id == identifier]
        if len(matches) != 1:
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        current = matches[0]
        if (
            current.name != name
            or current.uid_validity != validity
            or current.flags != 0
        ):
            raise MailWriteUncertain(FOLDER_UNCERTAIN)
        return self._bound(current)

    @staticmethod
    def _bound(current: MailboxSnapshot) -> MailFolder:
        """Do not fabricate message counts from a mutation acknowledgement."""
        return MailFolder(
            identifier=current.id, name=current.name, uidValidity=current.uid_validity
        )
