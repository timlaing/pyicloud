"""Test isolated locked Notes asset transport."""

from unittest.mock import MagicMock

import pytest
from requests import RequestException, Session

from pyicloud.common.cloudkit.models import CKAssetToken
from pyicloud.services.notes._locked_asset import download_locked_asset
from pyicloud.services.notes.exceptions import NotesError


@pytest.mark.parametrize(
    "url",
    [
        "http://cvws.icloud-content.com/B/test",
        "https://other.example/B/test",
        "https://user@cvws.icloud-content.com/B/test",
        "https://[invalid",
    ],
)
def test_reject_unsafe_asset_url(url: str) -> None:
    """Do not dispatch unsupported or ambiguous URLs."""
    with pytest.raises(NotesError, match="safely"):
        download_locked_asset(CKAssetToken(downloadURL=url, size=3))


@pytest.mark.parametrize(
    "case", ["success", "redirect", "short", "long", "encoded", "error"]
)
def test_locked_asset_transport(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send no credentials, follow no redirects, and enforce the exact size."""
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 302 if case == "redirect" else 200
    response.headers = {"Content-Encoding": "gzip"} if case == "encoded" else {}
    response.iter_content.return_value = {
        "short": [b"a"],
        "long": [b"ab", b"cd"],
    }.get(case, [b"a", b"bc"])
    send = MagicMock(return_value=response)
    if case == "error":
        send.side_effect = RequestException("synthetic signed URL")
    monkeypatch.setattr(Session, "send", send)
    asset = CKAssetToken(
        downloadURL="https://cvws.icloud-content.com/B/test/${f}?token=synthetic",
        size=3,
    )
    if case == "success":
        assert download_locked_asset(asset) == b"abc"
    else:
        with pytest.raises(NotesError, match="safely") as caught:
            download_locked_asset(asset)
        assert "synthetic" not in str(caught.value)
    send.assert_called_once()
    request = send.call_args.args[0]
    assert "Cookie" not in request.headers and "Authorization" not in request.headers
    assert send.call_args.kwargs == {
        "stream": True,
        "allow_redirects": False,
        "timeout": (10, 30),
        "verify": True,
        "proxies": {},
        "cert": None,
    }
