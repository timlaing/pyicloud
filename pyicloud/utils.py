"""Utils."""

import getpass
import sys
from typing import Optional

import keyring

KEYRING_SYSTEM = "pyicloud://icloud-password"


def get_password(username: str, interactive=sys.stdout.isatty()) -> Optional[str]:
    """Get the password from a username."""
    result: Optional[str] = get_password_from_keyring(username)
    if result:
        return result

    if interactive:
        return getpass.getpass(f"Enter iCloud password for {username}: ")

    # raise PyiCloudNoStoredPasswordAvailableException(
    #     f"No pyicloud password for {username} could be found "
    #     "in the system keychain.  Use the `--store-in-keyring` "
    #     "command-line option for storing a password for this "
    #     "username."
    # )


def password_exists_in_keyring(username: str) -> bool:
    """Return true if the password of a username exists in the keyring."""
    return get_password_from_keyring(username) is not None


def get_password_from_keyring(username: str) -> Optional[str]:
    """Get the password from a username."""
    return keyring.get_password(KEYRING_SYSTEM, username)


def store_password_in_keyring(username: str, password: str) -> None:
    """Store the password of a username."""
    return keyring.set_password(
        KEYRING_SYSTEM,
        username,
        password,
    )


def delete_password_in_keyring(username: str) -> None:
    """Delete the password of a username."""
    return keyring.delete_password(
        KEYRING_SYSTEM,
        username,
    )


def underscore_to_camelcase(word: str, initial_capital: bool = False) -> str:
    """Transform a word to camelCase."""
    words: list[str] = [x.capitalize() or "_" for x in word.split("_")]
    if not initial_capital:
        words[0] = words[0].lower()

    return "".join(words)
