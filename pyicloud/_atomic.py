"""Private same-directory atomic replacement for authentication files."""

from collections.abc import Generator
from contextlib import contextmanager, suppress
import os
import tempfile


@contextmanager
def atomic_path(filename: str) -> Generator[str, None, None]:
    """Stage a private file, then replace its destination after the writer closes.

    Atomic visibility is per file, not a cross-file transaction or a guarantee
    against power loss. The caller must close its writer before leaving the block.
    """
    destination = os.path.abspath(filename)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(destination)}.",
        suffix=".tmp",
        dir=os.path.dirname(destination),
    )
    try:
        os.close(descriptor)
        yield temporary
        os.replace(temporary, destination)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
