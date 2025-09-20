"""Tests for the PyiCloudCookieJar class and its handling of FMIP auth cookies."""

from io import StringIO
from unittest.mock import MagicMock, mock_open, patch

from pyicloud.cookie_jar import _FMIP_AUTH_COOKIE_NAME, PyiCloudCookieJar


def create_cookie_jar_with_cookie(
    filename, name, domain="example.com", path="/", value="test"
) -> PyiCloudCookieJar:
    """Create a PyiCloudCookieJar with a single cookie."""
    with (
        patch("builtins.open", new_callable=mock_open),
        patch("os.open", new_callable=mock_open),
    ):
        jar = PyiCloudCookieJar(filename=filename)
        jar.set(name, value, domain=domain, path=path)
        return jar


def test_load_no_filename() -> None:
    """Test that load is a no-op if no filename is set."""
    jar = PyiCloudCookieJar()
    # Should not raise or do anything if no filename is set
    # with patch("builtins.open", mock_open()):
    jar.load()  # No-op


def test_load_with_filename_removes_fmip_cookie() -> None:
    """Test that loading a jar with an FMIP cookie removes that cookie."""
    filename = "test_cookies.txt"
    buffer = StringIO()
    buffer.close = MagicMock()
    with (
        patch("builtins.open", new_callable=mock_open) as m,
        patch("os.open"),
        patch("os.fdopen") as os_fdopen,
    ):
        m.return_value = buffer
        os_fdopen.return_value = buffer

        jar: PyiCloudCookieJar = create_cookie_jar_with_cookie(
            filename, _FMIP_AUTH_COOKIE_NAME
        )
        # Add a non-FMIP cookie too
        jar.set("other_cookie", "value", domain="example.com", path="/")
        jar.save()
        # Reload and check FMIP cookie is removed
        jar2 = PyiCloudCookieJar(filename=filename)
        buffer.seek(0)
        jar2.load()
        names: list[str] = [cookie.name for cookie in jar2]
        assert _FMIP_AUTH_COOKIE_NAME not in names
        assert "other_cookie" in names


def test_load_with_custom_filename_argument_removes_fmip_cookie() -> None:
    """Test that loading a jar with an FMIP cookie removes that cookie."""
    filename = "test_cookies.txt"
    buffer = StringIO()
    buffer.close = MagicMock()
    with (
        patch("builtins.open", new_callable=mock_open) as m,
        patch("os.open"),
        patch("os.fdopen") as os_fdopen,
    ):
        m.return_value = buffer
        os_fdopen.return_value = buffer
        jar: PyiCloudCookieJar = create_cookie_jar_with_cookie(
            filename, _FMIP_AUTH_COOKIE_NAME
        )
        jar.save()

        jar2: PyiCloudCookieJar = PyiCloudCookieJar()
        names: list[str] = [cookie.name for cookie in jar2]
        assert _FMIP_AUTH_COOKIE_NAME not in names


def test_load_handles_keyerror_on_clear() -> None:
    """Test that load handles KeyError from clear gracefully."""
    filename = "test_cookies.txt"
    buffer = StringIO()
    buffer.close = MagicMock()
    with (
        patch("builtins.open", new_callable=mock_open) as m,
        patch("os.open"),
        patch("os.fdopen") as os_fdopen,
    ):
        m.return_value = buffer
        os_fdopen.return_value = buffer
        jar: PyiCloudCookieJar = create_cookie_jar_with_cookie(
            filename, _FMIP_AUTH_COOKIE_NAME
        )
        jar.save()
        buffer.seek(0)
        jar2: PyiCloudCookieJar = PyiCloudCookieJar(filename=filename)

        # Monkeypatch clear to raise KeyError
        def raise_keyerror(*args, **kwargs) -> None:
            raise KeyError

        with patch.object(jar2, "clear", side_effect=raise_keyerror):
            # Should not raise
            jar2.load()
