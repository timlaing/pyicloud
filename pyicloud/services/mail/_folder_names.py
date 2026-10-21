"""Browser-compatible mailbox leaf names and modified UTF-7 wire encoding."""

import base64
import re

_ENCODED_RUN = re.compile(r"[^\x20-\x7e]+")
SYSTEM_FOLDERS = frozenset(
    name.casefold()
    for name in (
        "INBOX",
        "Drafts",
        "Sent Messages",
        "Deleted Messages",
        "Junk",
        "Archive",
        "/VIP",
        "/REMIND_ME",
    )
)


def encode_leaf(name: str) -> str:
    """Validate one user-visible leaf, then encode the observed IMAP wire form."""
    if not isinstance(name, str) or not name.strip() or len(name) > 512:
        raise ValueError(
            "A non-blank folder leaf name of at most 512 characters is required"
        )
    if name.startswith(("#", "$category$_")) or any(c in name for c in "/?*%"):
        raise ValueError(
            "Folder leaf names cannot use reserved prefixes "
            "or hierarchy/wildcard characters"
        )
    if any(
        ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in name
    ):
        raise ValueError("Folder names cannot contain controls or unpaired surrogates")
    escaped = name.replace("&", "&-")

    def encode_run(match: re.Match[str]) -> str:
        """Encode one contiguous non-ASCII run without printable-ASCII shifts."""
        encoded = base64.b64encode(match[0].encode("utf-16-be")).decode("ascii")
        return "&" + encoded.rstrip("=").replace("/", ",") + "-"

    return _ENCODED_RUN.sub(encode_run, escaped)


def folder_path(leaf: str, parent: str = "") -> str:
    """Preserve a discovered parent's wire path; never silently normalize it."""
    encoded = encode_leaf(leaf)
    if not parent.isascii():
        raise ValueError("The discovered parent namespace is not a supported wire path")
    path = parent + "/" + encoded if parent else encoded
    if len(path) > 512:
        raise ValueError("The full encoded folder path must not exceed 512 characters")
    if not parent and path.casefold() in SYSTEM_FOLDERS:
        raise ValueError("A new folder name cannot replace a system mailbox")
    return path
