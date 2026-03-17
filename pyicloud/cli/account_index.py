"""Local account discovery index for the Typer CLI."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypedDict

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


def load_accounts(session_root: str | Path) -> dict[str, AccountIndexEntry]:
    """Load indexed accounts from disk."""

    index_path = account_index_path(session_root)
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


def _save_accounts(
    session_root: str | Path, accounts: dict[str, AccountIndexEntry]
) -> None:
    """Persist indexed accounts to disk."""

    index_path = account_index_path(session_root)
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
    index_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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

    accounts = load_accounts(session_root)
    retained = {
        username: entry
        for username, entry in accounts.items()
        if _is_discoverable(entry, keyring_has)
    }
    if retained != accounts:
        _save_accounts(session_root, retained)
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

    accounts = {
        entry["username"]: entry for entry in prune_accounts(session_root, keyring_has)
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
    _save_accounts(session_root, accounts)
    return entry
