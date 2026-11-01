"""Atomic replacement preserves old authentication files on failed writes."""

# pylint: disable=protected-access

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from pyicloud import PyiCloudService
from pyicloud._atomic import atomic_path
from pyicloud.cookie_jar import PyiCloudCookieJar
from pyicloud.session import NON_PERSISTED_SESSION_KEYS, PyiCloudSession


def write_staged(destination: Path, fail: bool) -> None:
    """Model a writer failing before its atomic transaction completes."""
    with atomic_path(str(destination)) as temporary:
        Path(temporary).write_text("partial", encoding="utf-8")
        if fail:
            raise OSError("write failed")


@pytest.fixture(name="directory")
def directory_fixture() -> Iterator[Path]:
    """Use only absolute sandbox paths, including during cleanup."""
    root = Path(tempfile.gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(dir=root))
    try:
        yield directory
    finally:
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem component limits")
def test_long_destination_basename_keeps_staging_name_bounded(directory: Path) -> None:
    """A valid near-limit destination must not acquire a longer staging basename."""
    destination = directory / ("x" * 250)
    destination.write_text("previous", encoding="utf-8")
    with atomic_path(str(destination)) as temporary:
        staged = Path(temporary)
        assert staged.parent == directory
        assert len(staged.name) < 40
        staged.write_text("replacement", encoding="utf-8")
    assert destination.read_text(encoding="utf-8") == "replacement"
    assert list(directory.iterdir()) == [destination]


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["write", "replace"])
def test_atomic_failure_preserves_destination(
    directory: Path, existing: bool, failure: str
) -> None:
    """Partial writes or failed replacement never publish a partial destination."""
    destination = directory / "session"
    if existing:
        destination.write_text("previous", encoding="utf-8")
    replacement = patch(
        "pyicloud._atomic.os.replace", side_effect=OSError("replace failed")
    )
    context = replacement if failure == "replace" else nullcontext()
    with context, pytest.raises(OSError):
        write_staged(destination, failure == "write")
    assert destination.exists() is existing
    if existing:
        assert destination.read_text(encoding="utf-8") == "previous"
    assert list(directory.iterdir()) == ([destination] if existing else [])


def test_session_roundtrip_and_permissions(
    pyicloud_service_working: PyiCloudService, directory: Path
) -> None:
    """Valid JSON and filtered keys retain their old reader contract."""
    session = PyiCloudSession(pyicloud_service_working, "client", str(directory))
    session.data.update({key: "do-not-save" for key in NON_PERSISTED_SESSION_KEYS})
    session.data["session_token"] = "synthetic"
    # Replacement must also tighten pre-existing files, without changing formats.
    for filename in (session.session_path, session.cookiejar_path):
        Path(filename).write_text("previous", encoding="utf-8")
        if os.name == "posix":
            Path(filename).chmod(0o644)
    session._save_session_data()
    saved = json.loads(Path(session.session_path).read_text(encoding="utf-8"))
    assert saved == {"client_id": "client", "session_token": "synthetic"}
    restored = PyiCloudSession(pyicloud_service_working, "fresh", str(directory))
    assert restored.data == saved
    if os.name == "posix":
        for filename in (session.session_path, session.cookiejar_path):
            assert Path(filename).stat().st_mode & 0o777 == 0o600


def test_json_serialisation_failure_retains_old_file(
    pyicloud_service_working: PyiCloudService, directory: Path
) -> None:
    """A serialisation failure still propagates and leaves the last session intact."""
    session = PyiCloudSession(pyicloud_service_working, "client", str(directory))
    destination = Path(session.session_path)
    destination.write_text('{"client_id":"previous"}', encoding="utf-8")
    with (
        patch("pyicloud.session.dump", side_effect=TypeError("cannot encode")),
        pytest.raises(TypeError, match="cannot encode"),
    ):
        session._save_session_data()
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "client_id": "previous"
    }
    assert list(directory.iterdir()) == [destination]


def test_writer_close_failure_preserves_destination(directory: Path) -> None:
    """A failure while leaving the writer context must abort replacement too."""
    destination = directory / "session"
    destination.write_text("previous", encoding="utf-8")

    @contextmanager
    def writer() -> Iterator[None]:
        yield
        raise OSError("close failed")

    def save() -> None:
        with atomic_path(str(destination)) as temporary, writer():
            Path(temporary).write_text("partial", encoding="utf-8")

    with pytest.raises(OSError, match="close failed"):
        save()
    assert destination.read_text(encoding="utf-8") == "previous"
    assert list(directory.iterdir()) == [destination]


def test_cookie_roundtrip_and_failed_save(directory: Path) -> None:
    """Cookie format/options remain loadable, and failures retain the previous jar."""
    destination = directory / "cookies"
    jar = PyiCloudCookieJar(str(destination))
    jar.set("test", "original", domain="example.com", expires=1)
    jar.save(ignore_discard=True, ignore_expires=True)
    restored = PyiCloudCookieJar(str(destination))
    restored.load(ignore_discard=True, ignore_expires=True)
    assert restored.get("test") == "original"
    previous = destination.read_bytes()
    with (
        patch(
            "pyicloud.cookie_jar.LWPCookieJar.save", side_effect=OSError("write failed")
        ),
        pytest.raises(OSError, match="write failed"),
    ):
        jar.save()
    assert destination.read_bytes() == previous
    assert list(directory.iterdir()) == [destination]
