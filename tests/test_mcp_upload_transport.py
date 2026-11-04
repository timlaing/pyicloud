"""Synthetic upload transport safety; no real account, URL or byte transfer."""

from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.upload_transport import (
    UploadTransport,
    bounded_upload_json,
    checked_upload_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://content.example.test/a",
        "https://other.test/a",
        "https://user:secret@content.example.test/a",
        "https://content.example.test:444/a",
        "https://content.example.test/a#fragment",
        "https://content.example.test/a\n",
        "https://content.example.test\\other/a",
        "https://content.example.test:bad/a",
    ],
)
def test_unsafe_targets_never_dispatch(url: str) -> None:
    """Returned URLs cannot expand trusted hosts or smuggle credentials."""
    hosts = frozenset({"content.example.test"})
    with pytest.raises(WriteDenied, match="unsupported_upload_route"):
        checked_upload_url(url, hosts)


@pytest.mark.parametrize("case", ["ok", "redirect", "large", "invalid", "read"])
def test_response_is_bounded_and_always_closed(case: str) -> None:
    """Redirects and uncertain JSON are not successful upload receipts."""
    response = MagicMock()
    response.status_code = 302 if case == "redirect" else 200
    response.iter_content.return_value = [b'{"ok":true}']
    if case == "large":
        response.iter_content.return_value = [b"x" * 262145]
    if case == "invalid":
        response.iter_content.return_value = [b"not-json"]
    if case == "read":
        response.iter_content.side_effect = OSError("private")
    if case == "ok":
        assert bounded_upload_json(response) == {"ok": True}
    else:
        with pytest.raises(WriteDenied, match="upload_outcome_uncertain"):
            bounded_upload_json(response)
    response.close.assert_called_once()


@pytest.mark.parametrize("case", ["ok", "transport", "bytes"])
def test_transfer_has_no_shared_auth_environment_redirect_or_retry(case: str) -> None:
    """Only approved immutable bytes reach one isolated synthetic dispatch."""
    transport = UploadTransport(frozenset({"content.example.test"}))
    session = MagicMock()
    response = session.post.return_value
    response.status_code = 200
    response.iter_content.return_value = [b'{"singleFile":{}}']
    if case == "transport":
        session.post.side_effect = OSError("private")
    with patch("pyicloud.mcp.upload_transport.Session") as factory:
        factory.return_value.__enter__.return_value = session
        if case == "ok":
            assert transport.transfer(
                "https://content.example.test/a", b"approved"
            ) == {"singleFile": {}}
        else:
            with pytest.raises(WriteDenied):
                transport.transfer(
                    "https://content.example.test/a",
                    b"" if case == "bytes" else b"approved",
                )
        if case == "bytes":
            factory.assert_not_called()
        else:
            assert session.trust_env is False
            adapter = session.mount.call_args.args[1]
            assert adapter.max_retries.total == 0
            assert adapter.requests_left == 1
            assert adapter.deadline is not None
            session.post.assert_called_once_with(
                "https://content.example.test/a",
                data=b"approved",
                timeout=(10.0, 60.0),
                allow_redirects=False,
                stream=True,
            )


@pytest.mark.parametrize("fail", [False, True])
def test_service_registration_is_one_raw_request(fail: bool) -> None:
    """Ordinary session auth recovery cannot replay a registration."""
    session = MagicMock()
    response = session.request_raw.return_value
    response.status_code = 200
    response.iter_content.return_value = [b"[]"]
    if fail:
        session.request_raw.side_effect = OSError("private")
    args = (
        session,
        "https://service.example.test/register",
        frozenset({"service.example.test"}),
        {"exact": True},
    )
    if fail:
        with pytest.raises(WriteDenied):
            UploadTransport.register(*args)
    else:
        assert UploadTransport.register(*args) == []
    session.request_raw.assert_called_once()
    session.post.assert_not_called()


@pytest.mark.parametrize("name", ["file.txt", "../file.txt"])
def test_multipart_transfer_keeps_filename_local_and_auth_isolated(name: str) -> None:
    """Empty Drive files are allowed; multipart names cannot select filesystem paths."""
    session = MagicMock()
    response = session.post.return_value
    response.status_code = 200
    response.iter_content.return_value = [b'{"singleFile":{}}']
    transport = UploadTransport(frozenset({"content.example.test"}))
    with patch("pyicloud.mcp.upload_transport.Session") as factory:
        factory.return_value.__enter__.return_value = session
        if name.startswith("../"):
            with pytest.raises(WriteDenied):
                transport.transfer(
                    "https://content.example.test/upload", b"", multipart_name=name
                )
            factory.assert_not_called()
        else:
            assert transport.transfer(
                "https://content.example.test/upload", b"", multipart_name=name
            ) == {"singleFile": {}}
            assert session.trust_env is False
            assert session.post.call_args.kwargs["files"] == {name: (name, b"")}
