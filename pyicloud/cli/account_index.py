"""Local account discovery index for the Typer CLI."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, TypedDict

ACCOUNT_INDEX_FILENAME = "accounts.json"


class AccountIndexEntry(TypedDict, total=False):
    """Persisted local account metadata."""

    username: str
    last_used_at: str
    session_path: str
    cookiejar_path: str
    china_mainland: bool


def account_index_path(session_root: str | Path) -> Path:
    """Return the JSON file path for the local account index."""

    return Path(session_root) / ACCOUNT_INDEX_FILENAME


def _load_accounts_from_path(index_path: Path) -> dict[str, AccountIndexEntry]:
    """Load indexed accounts from a specific path."""

    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}

    accounts = raw.get("accounts") if isinstance(raw, dict) else None
    if not isinstance(accounts, dict):
        return {}

    normalized: dict[str, AccountIndexEntry] = {}
    for username, entry in accounts.items():
        if not isinstance(username, str) or not isinstance(entry, dict):
            continue
        session_path = entry.get("session_path")
        cookiejar_path = entry.get("cookiejar_path")
        last_used_at = entry.get("last_used_at")
        if not isinstance(session_path, str) or not isinstance(cookiejar_path, str):
            continue
        normalized[username] = {
            "username": username,
            "last_used_at": last_used_at if isinstance(last_used_at, str) else "",
            "session_path": session_path,
            "cookiejar_path": cookiejar_path,
        }
        china_mainland = entry.get("china_mainland")
        if isinstance(china_mainland, bool):
            normalized[username]["china_mainland"] = china_mainland
    return normalized


def load_accounts(session_root: str | Path) -> dict[str, AccountIndexEntry]:
    """Load indexed accounts from disk."""

    return _load_accounts_from_path(account_index_path(session_root))


@contextmanager
def _locked_index(session_root: str | Path) -> Iterator[Path]:
    """Serialize account index updates when the platform supports file locking."""

    index_path = account_index_path(session_root)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = index_path.with_suffix(f"{index_path.suffix}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            import fcntl  # pylint: disable=import-outside-toplevel
        except ImportError:  # pragma: no cover - Windows fallback
            yield index_path
            return

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield index_path
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _save_accounts(
    session_root: str | Path, accounts: dict[str, AccountIndexEntry]
) -> None:
    """Persist indexed accounts to disk."""

    _save_accounts_to_path(account_index_path(session_root), accounts)


def _save_accounts_to_path(
    index_path: Path, accounts: dict[str, AccountIndexEntry]
) -> None:
    """Persist indexed accounts to a specific path."""

    if not accounts:
        try:
            index_path.unlink()
        except FileNotFoundError:
            pass
        return

    index_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "accounts": {username: accounts[username] for username in sorted(accounts)}
    }
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=index_path.parent,
            prefix=f".{index_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        os.replace(temp_path, index_path)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        raise


def _is_discoverable(
    entry: AccountIndexEntry, keyring_has: Callable[[str], bool]
) -> bool:
    return (
        Path(entry["session_path"]).exists()
        or Path(entry["cookiejar_path"]).exists()
        or keyring_has(entry["username"])
    )


def prune_accounts(
    session_root: str | Path, keyring_has: Callable[[str], bool]
) -> list[AccountIndexEntry]:
    """Drop stale entries and return discoverable accounts."""

    with _locked_index(session_root) as index_path:
        accounts = _load_accounts_from_path(index_path)
        retained = {
            username: entry
            for username, entry in accounts.items()
            if _is_discoverable(entry, keyring_has)
        }
        if retained != accounts:
            _save_accounts_to_path(index_path, retained)
    return [retained[username] for username in sorted(retained)]


def remember_account(
    session_root: str | Path,
    *,
    username: str,
    session_path: str,
    cookiejar_path: str,
    china_mainland: bool | None,
    keyring_has: Callable[[str], bool],
) -> AccountIndexEntry:
    """Upsert one account entry and prune any stale neighbors."""

    with _locked_index(session_root) as index_path:
        accounts = {
            username_: entry
            for username_, entry in _load_accounts_from_path(index_path).items()
            if _is_discoverable(entry, keyring_has)
        }
        previous = accounts.get(username)
        entry: AccountIndexEntry = {
            "username": username,
            "last_used_at": datetime.now(tz=timezone.utc).isoformat(),
            "session_path": session_path,
            "cookiejar_path": cookiejar_path,
        }
        if china_mainland is not None:
            entry["china_mainland"] = china_mainland
        elif previous is not None and "china_mainland" in previous:
            entry["china_mainland"] = previous["china_mainland"]
        accounts[username] = entry
        _save_accounts_to_path(index_path, accounts)
        return entry
