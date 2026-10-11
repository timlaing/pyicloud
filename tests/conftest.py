"""Pytest configuration file for the pyicloud package."""
# pylint: disable=redefined-outer-name,protected-access
# pylint: disable=protected-access
# pylint: disable=redefined-outer-name

from collections.abc import Generator, Iterator
from contextlib import ExitStack, contextmanager, suppress
from contextvars import ContextVar
import io
import os
import pathlib
import secrets
import shutil
import sys
import tempfile
from types import FrameType
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest
from requests.cookies import RequestsCookieJar

from pyicloud.base import PyiCloudService
from pyicloud.services.contacts import ContactsService
from pyicloud.services.drive import COOKIE_APPLE_WEBAUTH_VALIDATE
from pyicloud.services.hidemyemail import HideMyEmailService
from pyicloud.services.photos import (
    AlbumContainer,
    BasePhotoAlbum,
    BasePhotoLibrary,
    DirectionEnum,
    ListTypeEnum,
    PhotoAsset,
)
from pyicloud.session import PyiCloudSession
from tests import PyiCloudSessionMock
from tests.const import LOGIN_WORKING
from tests.fixture_loader import fixture_read_allowed

BUILTINS_OPEN: str = "builtins.open"
EXAMPLE_DOMAIN: str = "https://example.com"


class FileSystemAccessError(Exception):
    """Raised when a test tries to access the file system."""


def normalize_path(path: Any) -> str:
    """Normalize PathLike objects before file-system guard checks."""

    try:
        return str(os.fspath(path))
    except TypeError:
        return str(path)


SANDBOX_DIRNAME: str = "python-test-results"


def _sandbox_roots() -> frozenset[str]:
    """Absolute and symlink-resolved roots tests are allowed to touch."""

    base = os.path.join(tempfile.gettempdir(), SANDBOX_DIRNAME)
    roots = {os.path.abspath(base)}
    with suppress(OSError):
        roots.add(os.path.realpath(base))
    return frozenset(roots)


_SANDBOX_ROOTS: frozenset[str] = _sandbox_roots()


_RESOLVING: ContextVar[bool] = ContextVar("_resolving", default=False)


def _matches_sandbox_root(candidate: str, *, allow_ancestors: bool) -> bool:
    """Return True when ``candidate`` is inside (or, optionally, above) a root."""

    for root in _SANDBOX_ROOTS:
        if candidate == root or candidate.startswith(root + os.sep):
            return True
        if allow_ancestors and root.startswith(candidate + os.sep):
            return True
    return allow_ancestors and candidate == os.sep


def _resolves_into_sandbox(candidate: str) -> bool:
    """Return True when ``candidate`` resolves to a path inside the sandbox.

    A symlink inside the sandbox can point outside it, so the lexical prefix
    check is not enough on its own.  ``os.path.realpath`` walks the path with
    ``os.lstat``/``os.readlink``, which the guards patch; ``_RESOLVING`` stops
    the guard re-entering while it runs, and any resolution failure is treated
    as outside the sandbox.
    """

    token = _RESOLVING.set(True)
    try:
        resolved = os.path.realpath(candidate)
    except (OSError, FileSystemAccessError):
        return False
    finally:
        _RESOLVING.reset(token)
    return _matches_sandbox_root(resolved, allow_ancestors=False)


def in_sandbox(path: Any, *, allow_ancestors: bool = False) -> bool:
    """Return True when ``path`` points inside the test sandbox.

    The check is a resolved-prefix comparison rather than a substring match, so
    a sibling such as ``/tmp/python-test-results-backup`` is *not* allowed, and
    a symlink inside the sandbox that points outside it resolves to a rejected
    path.

    ``allow_ancestors`` additionally permits directories that contain the
    sandbox, which ``Path.resolve`` must inspect on its way down to a sandbox
    path.
    """

    candidate = os.path.abspath(normalize_path(path))
    if not _matches_sandbox_root(candidate, allow_ancestors=allow_ancestors):
        return False
    if allow_ancestors:
        return True
    return _resolves_into_sandbox(candidate)


def _is_message_catalog(path: Any) -> bool:
    """Return True for gettext catalogs read by click/gettext.

    Click (via Typer) lazily imports translated help text, which stats and opens
    ``LC_MESSAGES/*.mo`` files.  That lookup is library behaviour rather than
    test file access, so it is allowed for click/gettext callers only.
    """

    if not normalize_path(path).endswith(".mo"):
        return False
    frame: FrameType | None = sys._getframe()
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module == "gettext" or "click" in module:
            return True
        frame = frame.f_back
    return False


def _is_coverage_collecting() -> bool:
    """Return True when file access comes from coverage collecting data.

    With branch coverage enabled, coverage's ``sys.monitoring`` backend reads
    and tokenizes source files to build a branch map while a test runs.  That is
    tooling behaviour rather than a test touching the disk, so it is allowed for
    coverage callers only.
    """

    frame: FrameType | None = sys._getframe()
    while frame is not None:
        module = frame.f_globals.get("__name__", "")
        if module == "coverage" or module.startswith(("coverage.", "pytest_cov")):
            return True
        frame = frame.f_back
    return False


_PROJECT_ROOT: str = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_PACKAGE_SOURCE_ROOT: str = os.path.join(_PROJECT_ROOT, "pyicloud")
_COVERAGE_REPORT_PATH: str = os.path.join(_PROJECT_ROOT, "coverage.xml")
_SOURCE_READ: ContextVar[bool] = ContextVar("_source_read", default=False)


def _is_package_source(path: Any) -> bool:
    """Return True when ``path`` is one of the library's own source files."""

    candidate = os.path.abspath(normalize_path(path))
    return candidate == _PACKAGE_SOURCE_ROOT or candidate.startswith(
        _PACKAGE_SOURCE_ROOT + os.sep
    )


def source_read_allowed(path: Any) -> bool:
    """Return True for package-source reads a scanning test has sanctioned."""

    return _SOURCE_READ.get() and _is_package_source(path)


@contextmanager
def allow_source_reads() -> Generator[None, None, None]:
    """Sanction reads of the library's own sources for the inventory scan.

    ``tests/test_endpoints.py`` parses the package at test time rather than at
    import time, so the guard stays in force and the scan declares its reads
    here instead of slipping past the checks.
    """

    token = _SOURCE_READ.set(True)
    try:
        yield
    finally:
        _SOURCE_READ.reset(token)


def _coverage_access_allowed(
    path: Any,
    *,
    operation: str,
    mode: str | None = None,
    flags: int | None = None,
) -> bool:
    """Return True for the narrow file access coverage itself needs.

    Coverage's ``sys.monitoring`` backend canonicalizes every executed file
    (``realpath`` on stdlib paths included), reads and tokenizes the measured
    package sources, and its report writer emits ``coverage.xml``.  Only those
    shapes are sanctioned, so a coverage frame cannot authorise arbitrary
    access:

    * ``stat``/``access`` metadata resolution is allowed anywhere, because
      canonicalizing a filename is path-only and cannot read file contents;
    * content reads (``open``/``listdir``/``walk``/...) must target package
      sources;
    * writes must be *write-only* to the report path.

    Mutations and two-path operations never qualify.
    """

    if not _is_coverage_collecting():
        return False
    if operation == "stat":
        return True
    if operation == "read":
        return _is_package_source(path)
    if operation != "write":
        return False
    if os.path.abspath(normalize_path(path)) != _COVERAGE_REPORT_PATH:
        return False
    if mode is not None:
        return mode.startswith("w") and "+" not in mode
    if flags is not None:
        return flags & _WRITE_FLAGS == os.O_WRONLY
    return False


def _reject(
    action: str,
    path: Any,
    *,
    allow_ancestors: bool = False,
    operation: str = "other",
    mode: str | None = None,
    flags: int | None = None,
) -> None:
    """Raise :class:`FileSystemAccessError` when ``path`` is outside sandbox."""

    if _RESOLVING.get():
        return
    if in_sandbox(path, allow_ancestors=allow_ancestors):
        return
    if fixture_read_allowed():
        return
    # The inventory scan is a read-only exemption: it may read and inspect
    # package sources (``stat``/``listdir``/...), but never write or mutate them.
    if operation in ("read", "stat") and source_read_allowed(path):
        return
    if _is_message_catalog(path):
        return
    if _coverage_access_allowed(path, operation=operation, mode=mode, flags=flags):
        return
    raise FileSystemAccessError(
        f"You should not {action} in tests. {normalize_path(path)}"
    )


def _guard_path(
    original: Any,
    action: str,
    *,
    allow_ancestors: bool = False,
    operation: str = "other",
) -> Any:
    """Wrap a one-path function so non-sandbox access raises."""

    def guarded(path: Any, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(path, int):
            _reject(
                action,
                path,
                allow_ancestors=allow_ancestors,
                operation=operation,
            )
        return original(path, *args, **kwargs)

    return guarded


def _guard_open(original: Any, action: str) -> Any:
    """Wrap ``open``-style functions, classifying read vs. write access."""

    def guarded(path: Any, *args: Any, **kwargs: Any) -> Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        if not isinstance(path, int):
            operation = "write" if any(c in mode for c in "wax+") else "read"
            _reject(action, path, operation=operation, mode=mode)
        return original(path, *args, **kwargs)

    return guarded


def _guard_os_open(original: Any, action: str) -> Any:
    """Wrap ``os.open`` so its access flags are classified for the guard."""

    def guarded(path: Any, *args: Any, **kwargs: Any) -> Any:
        flags = args[0] if args else kwargs.get("flags", os.O_RDONLY)
        if not isinstance(path, int):
            operation = "read" if (flags & _WRITE_FLAGS) == 0 else "write"
            _reject(action, path, operation=operation, flags=flags)
        return original(path, *args, **kwargs)

    return guarded


def _guard_two_paths(original: Any, action: str) -> Any:
    """Wrap a two-path function so non-sandbox access raises."""

    def guarded(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
        for path in (src, dst):
            if not isinstance(path, int):
                _reject(action, path)
        return original(src, dst, *args, **kwargs)

    return guarded


@pytest.fixture(autouse=True)
def mock_file_open_write_fixture() -> Any:
    """Mock the open function to prevent file system access."""
    # Dictionary to store written data
    written_data: dict[str, Any] = {}

    def mock_file_open(
        filepath: str,
        mode: str = "r",
        **_: Any,
    ) -> Any:
        """Mock file open function."""

        if "w" in mode or "a" in mode:
            # Writing or appending mode
            def mock_write(content: str) -> None:
                if filepath not in written_data:
                    written_data[filepath] = ""
                if "a" in mode:  # Append mode
                    written_data[filepath] += content
                else:  # Write mode
                    written_data[filepath] = content

            mock_file = mock_open().return_value
            mock_file.write = mock_write
            return mock_file
        if "r" in mode:
            raise FileNotFoundError(f"No such file or directory: '{filepath}'")

        raise ValueError(f"Unsupported mode: {mode}")

    # Attach the written_data dictionary to the mock for access in tests
    mock_file_open.written_data = written_data  # type: ignore
    return mock_file_open


@pytest.fixture(autouse=True)
def mock_mkdir() -> Iterator[Any]:
    """Mock the mkdir function to prevent file system access."""
    mkdir = os.mkdir

    def my_mkdir(path: Any, *args: Any, **kwargs: Any) -> None:
        _reject("be creating directories", path)
        return mkdir(path, *args, **kwargs)

    with patch("os.mkdir", my_mkdir) as mkdir_mock:
        yield mkdir_mock


@pytest.fixture(autouse=True)
def mock_makedirs() -> Iterator[Any]:
    """Mock the makedirs function to prevent file system access."""
    mkdirs = os.makedirs

    def my_makedirs(path: Any, *args: Any, **kwargs: Any) -> None:
        _reject("be creating directories", path)
        return mkdirs(path, *args, **kwargs)

    with patch("os.makedirs", my_makedirs) as mkdir_mock:
        yield mkdir_mock


@pytest.fixture(autouse=True)
def mock_chmod() -> Iterator[Any]:
    """Mock the chmod function to prevent file system access."""
    chmod = os.chmod

    def my_chmod(path: Any, *args: Any, **kwargs: Any) -> None:
        _reject("be changing file permissions", path)
        return chmod(path, *args, **kwargs)

    with patch("os.chmod", my_chmod) as chmod_mock:
        yield chmod_mock


@pytest.fixture(autouse=True, scope="session")
def mock_open_fixture() -> Iterator[Any]:
    """Mock the open function to prevent file system access."""
    with patch(BUILTINS_OPEN, _guard_open(open, "be opening files")) as open_mock:
        yield open_mock


@pytest.fixture(autouse=True, scope="session")
def mock_os_open_fixture() -> Iterator[Any]:
    """Mock the open function to prevent file system access."""
    with patch("os.open", _guard_os_open(os.open, "be opening files")) as open_mock:
        yield open_mock


_READ_ONLY_OS: tuple[str, ...] = (
    "stat",
    "lstat",
    "listdir",
    "scandir",
    "walk",
    "access",
    "readlink",
)
# ``Path.resolve`` stats and reads links up to the root, including ancestors of
# the sandbox, so those metadata calls may look above the sandbox boundary.
_READ_ONLY_OS_ANCESTORS: tuple[str, ...] = ("stat", "lstat", "readlink")
# Metadata-only calls resolve names without reading file contents, so coverage
# may use them (``realpath`` on stdlib paths, say) even outside package sources.
_READ_ONLY_OS_METADATA: frozenset[str] = frozenset({
    "stat",
    "lstat",
    "readlink",
    "access",
})
# ``os.O_RDONLY`` is 0, so mask the write bits to classify ``os.open`` flags.
_WRITE_FLAGS: int = os.O_WRONLY | os.O_RDWR
_MUTATION_OS: tuple[str, ...] = (
    "remove",
    "unlink",
    "rmdir",
    "removedirs",
    "truncate",
    "utime",
)
_MUTATION_OS_TWO_PATH: tuple[str, ...] = ("rename", "replace", "link", "symlink")
_SHUTIL_ONE_PATH: tuple[str, ...] = ("rmtree",)
_SHUTIL_TWO_PATH: tuple[str, ...] = (
    "copyfile",
    "copy",
    "copy2",
    "copymode",
    "copystat",
    "copytree",
    "move",
)


@pytest.fixture(autouse=True)
def block_io_open() -> Iterator[None]:
    """Block ``io.open``/``pathlib`` file I/O so it cannot bypass the guards."""
    guard = _guard_open(io.open, "call io.open")
    with ExitStack() as stack:
        stack.enter_context(patch("io.open", guard))
        # Python 3.10's ``Path.open`` reaches ``io.open`` through a captured
        # ``_NormalAccessor`` reference, so patch the method itself as well.
        stack.enter_context(
            patch.object(
                pathlib.Path,
                "open",
                _guard_open(pathlib.Path.open, "call Path.open"),
            )
        )
        yield


@contextmanager
def _readonly_guards() -> Generator[None, None, None]:
    """Patch read-only ``os`` entry points for the duration of the block.

    These are installed around the test call (not as a fixture) so pytest can
    still call ``Path.exists`` while importing the test module and while
    formatting a failure traceback, once the guards have been removed.
    """

    with ExitStack() as stack:
        for name in _READ_ONLY_OS:
            func = getattr(os, name)
            stack.enter_context(
                patch(
                    f"os.{name}",
                    _guard_path(
                        func,
                        f"call os.{name}",
                        allow_ancestors=name in _READ_ONLY_OS_ANCESTORS,
                        operation="stat" if name in _READ_ONLY_OS_METADATA else "read",
                    ),
                )
            )
        yield


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[Any]:  # pylint: disable=unused-argument
    """Block read-only file-system inspection during the test call."""
    with _readonly_guards():
        return (yield)


@pytest.fixture(autouse=True)
def block_file_system_mutations() -> Iterator[None]:
    """Block file-system mutations outside the sandbox."""
    with ExitStack() as stack:
        for name in _MUTATION_OS:
            func = getattr(os, name)
            stack.enter_context(
                patch(f"os.{name}", _guard_path(func, f"call os.{name}"))
            )
        for name in _MUTATION_OS_TWO_PATH:
            func = getattr(os, name)
            stack.enter_context(
                patch(f"os.{name}", _guard_two_paths(func, f"call os.{name}"))
            )
        yield


@pytest.fixture(autouse=True)
def block_shutil_file_system() -> Iterator[None]:
    """Block ``shutil`` file-system operations outside the sandbox."""
    with ExitStack() as stack:
        for name in _SHUTIL_ONE_PATH:
            func = getattr(shutil, name)
            stack.enter_context(
                patch(f"shutil.{name}", _guard_path(func, f"call shutil.{name}"))
            )
        for name in _SHUTIL_TWO_PATH:
            func = getattr(shutil, name)
            stack.enter_context(
                patch(
                    f"shutil.{name}",
                    _guard_two_paths(func, f"call shutil.{name}"),
                )
            )
        yield


@pytest.fixture
def pyicloud_service() -> PyiCloudService:
    """Create a PyiCloudService instance with mocked authenticate method."""
    with (
        patch("pyicloud.PyiCloudService.authenticate") as mock_authenticate,
        patch(
            "pyicloud.PyiCloudService._setup_cookie_directory"
        ) as mock_setup_cookie_directory,
        patch(BUILTINS_OPEN, new_callable=mock_open),
    ):
        # Mock the authenticate method during initialization
        mock_authenticate.return_value = None
        mock_setup_cookie_directory.return_value = "/tmp/pyicloud/cookies"
        service = PyiCloudService("test@example.com", secrets.token_hex(32))
        return service


@pytest.fixture
def pyicloud_service_working(
    pyicloud_service: PyiCloudService,
    monkeypatch: pytest.MonkeyPatch,
) -> PyiCloudService:
    """Set the service to a working state."""
    pyicloud_service.data = LOGIN_WORKING
    pyicloud_service._webservices = LOGIN_WORKING["webservices"]
    monkeypatch.setattr(
        pyicloud_service,
        "_check_pcs_consent",
        MagicMock(
            return_value={
                "isICDRSDisabled": False,
                "isDeviceConsentedForPCS": True,
            }
        ),
    )
    with patch(BUILTINS_OPEN, new_callable=mock_open):
        pyicloud_service._session = PyiCloudSessionMock(
            pyicloud_service,
            "",
            cookie_directory="",
        )
        pyicloud_service.session._data = {"session_token": "valid_token"}

    return pyicloud_service


@pytest.fixture
def pyicloud_session(pyicloud_service_working: PyiCloudService) -> PyiCloudSession:
    """Mock the PyiCloudSession class."""
    pyicloud_service_working.session.cookies = MagicMock()
    return pyicloud_service_working.session


@pytest.fixture
def mock_session() -> MagicMock:
    """Fixture to create a mock PyiCloudSession."""
    return MagicMock(spec=PyiCloudSession)


@pytest.fixture
def contacts_service(mock_session: MagicMock) -> ContactsService:
    """Fixture to create a ContactsService instance."""
    return ContactsService(
        service_root=EXAMPLE_DOMAIN,
        session=mock_session,
        params={"test_param": "value"},
    )


@pytest.fixture
def mock_photos_service() -> MagicMock:
    """Fixture for mocking PhotosService.

    The session is a ``MagicMock``, which ``_can_use_typed_cloudkit`` treats as
    mock-like, so libraries built on this fixture take the untyped request path
    rather than the typed CloudKit client. Tests that need the typed path pass a
    plain object as the session instead.
    """
    service = MagicMock()
    service.service_endpoint = EXAMPLE_DOMAIN
    service.params = {"dsid": "12345"}
    service.session = MagicMock()
    return service


@pytest.fixture
def mock_photo_library(mock_photos_service: MagicMock) -> BasePhotoLibrary:
    """Fixture for mocking PhotoLibrary."""

    class MyPhotoLibrary(BasePhotoLibrary):
        """Concrete implementation of BasePhotoLibrary for testing."""

        def _get_photo_payload(self, photo_id: str) -> Any:
            """Mock implementation of _get_photo_payload."""
            raise NotImplementedError()

        def _get_photo(self, photo_id: str) -> PhotoAsset:
            """Mock implementation of _get_photo."""
            raise NotImplementedError()

        def _get_albums(self) -> AlbumContainer:
            raise NotImplementedError()

    return MyPhotoLibrary(
        service=mock_photos_service,
        asset_type=PhotoAsset,
    )


@pytest.fixture
def mock_photo_album(mock_photos_service: MagicMock) -> BasePhotoAlbum:
    """Returns a mock BasePhotoAlbum subclass for testing."""

    class MyPhotoAlbum(BasePhotoAlbum):
        """Mock BasePhotoAlbum subclass for testing."""

        def _get_len(self) -> int:
            return 0

        def _get_payload(
            self, offset: int, page_size: int, direction: DirectionEnum
        ) -> dict[str, Any]:
            return {}

        def _get_url(self) -> str:
            return "https://example.com/test_album"

        def _get_photo_payload(self, photo_id: str) -> dict[str, Any]:
            return {}

        @property
        def fullname(self) -> str:
            return "Test Album"

        @property
        def id(self) -> str:
            return "test_album"

    return MyPhotoAlbum(
        library=mock_photos_service,
        name="Test Album",
        list_type=ListTypeEnum.DEFAULT,
    )


@pytest.fixture
def hidemyemail_service(mock_session: MagicMock) -> HideMyEmailService:
    """Fixture for initializing HideMyEmailService."""
    return HideMyEmailService(EXAMPLE_DOMAIN, mock_session, {"dsid": "12345"})


@pytest.fixture
def mock_service_with_cookies(
    pyicloud_service_working: PyiCloudService,
) -> PyiCloudService:
    """Fixture to create a mock PyiCloudService with cookies."""
    jar = RequestsCookieJar()
    jar.set(COOKIE_APPLE_WEBAUTH_VALIDATE, "t=768y9u", domain="icloud.com", path="/")

    # Attach a real CookieJar so code that calls `.cookies.get()` keeps working.
    pyicloud_service_working.session.cookies = jar

    return pyicloud_service_working


@pytest.fixture(autouse=True, scope="session")
def mock_thread() -> Iterator[Any]:
    """Mock threading.Thread to prevent actual thread creation during tests."""
    with patch("threading.Thread") as mock_thread_class:
        yield mock_thread_class
