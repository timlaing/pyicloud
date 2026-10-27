"""Synthetic Mail Drop capability validation, isolation, and atomic publication."""

from collections.abc import Iterator
import json
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from requests import ReadTimeout, Response, Session
from typer.testing import CliRunner

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIState
from pyicloud.services.mail import MailError, MailFolder, MailMessage, MailService
from tests.fixture_loader import load_fixture

Case = tuple[
    MailService, MagicMock, Session, MagicMock, MailMessage, MagicMock, MagicMock
]


@pytest.fixture(name="case")
def case_fixture() -> Iterator[Case]:
    """An owned account transport and a separately constructed anonymous transport."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    record = load_fixture("mail", "maildrop_message_response.json")["domainObjects"][0]
    message = MailMessage.model_validate({**record, "folder": folder})
    owned = MagicMock()
    owned.headers = {
        "Authorization": "synthetic-account-secret",
        "Cookie": "synthetic-cookie",
    }
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.headers = {"Content-Type": "video/quicktime", "Content-Length": "5"}
    response.iter_content.return_value = iter([b"mo", b"", b"vie"])
    service = MailService("https://example.test", owned, {"dsid": "synthetic-account"})
    external = Session()
    with (
        patch.object(service, "folders", return_value=[folder]) as folders,
        patch("pyicloud.services.mail.service.Session", return_value=external),
        patch.object(external, "send", return_value=response) as get,
        patch.object(external, "close", wraps=external.close) as close,
    ):
        yield service, owned, external, response, message, folders, get
        assert close.call_count <= 1


@pytest.fixture(name="output")
def output_fixture() -> Iterator[Path]:
    """Keep outputs in the test sandbox and remove private staging leftovers."""
    root = Path(gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as directory:
        try:
            yield Path(directory) / "chosen.mov"
        finally:
            for child in Path(directory).iterdir():
                child.unlink()


def consume(case: Case, message: MailMessage | None = None, **options: Any) -> bytes:
    """Exercise the public context-managed stream without saving content."""
    with case[0].mail_drop_attachment(
        message or case[4], options.pop("part_index", 2), **options
    ) as chunks:
        return b"".join(chunks)


def invoke(case: Case, output: Path, mode: str = "json") -> Any:
    """Use real download/publication with only account and exact lookup mocked."""
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = case[0]
    with (
        patch.object(CLIState, "open"),
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(case[0], "message", return_value=case[4]),
    ):
        return CliRunner().invoke(
            app,
            [
                "mail",
                "download-maildrop",
                case[4].id,
                "--folder-id",
                "10",
                "--part-index",
                "2",
                "--output",
                str(output),
                "--format",
                mode,
                "--max-bytes",
                "5",
            ],
        )


def changed_message(case: Case, **fields: Any) -> MailMessage:
    """Change one attachment property without sharing mutable fixture state."""
    message = case[4]
    return message.model_copy(
        update={"parts": [message.parts[0], message.parts[1].model_copy(update=fields)]}
    )


def test_anonymous_transport_and_byte_preservation(
    case: Case, monkeypatch: Any
) -> None:
    """Do not forward account auth, cookies, params, proxies, or remote filenames."""
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    assert consume(case) == b"movie"
    _, owned, external, response, message, folders, get = case
    assert external.trust_env is False
    assert external.auth is None
    assert not external.cookies
    assert not external.params
    assert "Authorization" not in external.headers
    assert "Cookie" not in external.headers
    assert "Referer" not in external.headers
    request = get.call_args.args[0]
    assert request.method == "GET"
    assert request.url == message.parts[1].mail_drop_url
    assert request.headers["Accept-Encoding"] == "identity"
    assert "Authorization" not in request.headers
    assert "Cookie" not in request.headers
    assert get.call_args.kwargs == {
        "stream": True,
        "allow_redirects": False,
        "timeout": (10.0, 60.0),
        "verify": True,
        "proxies": {},
        "cert": None,
    }
    assert folders.call_count == 3
    owned.get.assert_not_called()
    owned.post.assert_not_called()
    response.close.assert_called_once()


def test_explicit_ca_bundle_keeps_tls_verification(
    case: Case, monkeypatch: Any
) -> None:
    """An explicit trusted CA path is allowed without enabling environment auth."""
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/synthetic/ca.pem")
    assert consume(case) == b"movie"
    assert case[6].call_args.kwargs["verify"] == "/synthetic/ca.pem"


@pytest.mark.parametrize("ca_bundle", [None, "/synthetic/ca.pem"])
def test_cli_ssl_and_proxy_settings_cannot_override_download(
    case: Case, output: Path, monkeypatch: Any, ca_bundle: str | None
) -> None:
    """Exercise the real CLI SSL context through preparation to the transport sink."""
    if ca_bundle is None:
        monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    else:
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", ca_bundle)
    monkeypatch.setenv("HTTPS_PROXY", "http://environment-proxy.invalid:8080")
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = case[0]
    with (
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(case[0], "message", return_value=case[4]),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "download-maildrop",
                case[4].id,
                "--folder-id",
                "10",
                "--part-index",
                "2",
                "--output",
                str(output),
                "--format",
                "json",
                "--no-verify-ssl",
                "--http-proxy",
                "http://cli-proxy.invalid:8080",
                "--https-proxy",
                "http://cli-proxy.invalid:8080",
            ],
        )
    assert result.exit_code == 0, result.exception
    assert output.read_bytes() == b"movie"
    settings = case[6].call_args.kwargs
    assert settings["verify"] == (ca_bundle or True)
    assert settings["proxies"] == {}
    assert settings["cert"] is None
    assert settings["allow_redirects"] is False
    request = case[6].call_args.args[0]
    assert "Authorization" not in request.headers
    assert "Cookie" not in request.headers
    assert case[2].trust_env is False


@pytest.mark.parametrize("mode", ["json", "text"])
def test_cli_output_is_private_and_contains_no_capabilities(
    case: Case, output: Path, mode: str
) -> None:
    """Publish only complete bytes and never print signed URLs or embedded IDs."""
    result = invoke(case, output, mode)
    assert result.exit_code == 0, result.exception
    assert output.read_bytes() == b"movie"
    assert output.stat().st_mode & 0o777 == 0o600
    assert list(output.parent.iterdir()) == [output]
    assert "cvws" not in result.stdout
    assert "cloudpart:" not in result.stdout
    if mode == "json":
        assert json.loads(result.stdout)["size"] == 5


@pytest.mark.parametrize(
    "kind",
    [
        "http",
        "foreign",
        "suffix",
        "userinfo",
        "empty_userinfo",
        "port",
        "invalid_port",
        "invalid_ipv6",
        "fragment",
        "path",
        "control",
        "space",
        "account",
        "zone",
        "missing_signature",
        "expired",
        "invalid_expiry",
        "duplicate_expiry",
    ],
)
def test_invalid_urls_never_contact_content_host(case: Case, kind: str) -> None:
    """Treat signed URLs as untrusted capabilities, not arbitrary GET targets."""
    url = case[4].parts[1].mail_drop_url
    assert url is not None
    replacements = {
        "http": ("https:", "http:"),
        "foreign": ("cvws.icloud-content.com", "example.test"),
        "suffix": ("cvws.icloud-content.com", "cvws.icloud-content.com.evil.test"),
        "userinfo": ("//cvws", "//user:password@cvws"),
        "empty_userinfo": ("//cvws", "//@cvws"),
        "port": (".com/B/", ".com:444/B/"),
        "invalid_port": (".com/B/", ".com:invalid/B/"),
        "invalid_ipv6": ("//cvws", "//[cvws"),
        "fragment": ("s=fake", "s=fake#fragment"),
        "path": ("/B/", "/other/"),
        "control": ("movie.mov", "movie\n.mov"),
        "space": ("https:", " https:"),
        "account": ("com.apple.largeattachment", "com.apple.photos.cloud"),
        "zone": ("Apple-Webmail", "Other"),
        "missing_signature": ("&s=fake", ""),
        "expired": ("4102444800", "1"),
        "invalid_expiry": ("e=4102444800", "e=bad"),
        "duplicate_expiry": ("e=4102444800", "e=4102444800&e=4102444800"),
    }
    old, new = replacements[kind]
    message = changed_message(case, mail_drop_url=url.replace(old, new))
    with pytest.raises(MailError):
        consume(case, message)
    case[6].assert_not_called()
    case[5].assert_not_called()


@pytest.mark.parametrize(
    "fields",
    [
        {"is_attachment": False},
        {"mail_drop_url": None},
        {"id": "2"},
        {"mail_drop_expiration_ms": None},
        {"mail_drop_expiration_ms": 1},
        {"size": 1000},
    ],
)
def test_invalid_attachment_metadata_is_not_downloaded(
    case: Case, fields: dict[str, Any]
) -> None:
    """Require a cloud-part reference, expiration, and an acceptable byte budget."""
    message = changed_message(case, **fields)
    with pytest.raises((MailError, ValueError)):
        consume(case, message, max_bytes=10)
    case[6].assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("part_index", True),
        ("part_index", 0),
        ("part_index", 3),
        ("part_index", "2"),
        ("max_bytes", True),
        ("max_bytes", 0),
        ("max_bytes", "5"),
    ],
)
def test_invalid_selectors_and_budgets_fail_before_http(
    case: Case, field: str, value: Any
) -> None:
    """Reject invalid caller inputs before checking folders or creating a stream."""
    with pytest.raises(ValueError):
        consume(case, **{field: value})
    case[6].assert_not_called()
    case[5].assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/html"},
        {"Content-Type": "application/json"},
        {"Content-Type": ""},
        {"Content-Type": "video/quicktime", "Content-Encoding": "gzip"},
        {"Content-Type": "video/quicktime", "Content-Length": "bad"},
        {"Content-Type": "video/quicktime", "Content-Length": "6"},
        {"Content-Type": "video/quicktime", "Content-Length": "1000"},
    ],
)
def test_invalid_headers_never_publish_files(
    case: Case, output: Path, headers: dict[str, str]
) -> None:
    """Reject unexpected type, compressed bodies, and inconsistent/excessive lengths."""
    case[3].headers = headers
    result = invoke(case, output)
    assert result.exit_code != 0
    assert not list(output.parent.iterdir())
    case[3].iter_content.assert_not_called()
    case[3].close.assert_called_once()


@pytest.mark.parametrize("status", [302, 403, 404, 500])
def test_redirects_and_failed_responses_are_not_followed(
    case: Case, output: Path, status: int
) -> None:
    """Only a direct HTTP 200 can supply bytes; no redirect target is contacted."""
    case[3].status_code = status
    result = invoke(case, output)
    assert result.exit_code != 0
    assert case[6].call_count == 1
    assert not list(output.parent.iterdir())
    case[3].close.assert_called_once()


@pytest.mark.parametrize("kind", ["oversize", "truncated", "transport"])
def test_late_failures_remove_staging(case: Case, output: Path, kind: str) -> None:
    """A successful response or first chunk cannot imply a successful download."""
    case[3].headers = {"Content-Type": "video/quicktime"}
    message = case[4]
    if kind == "oversize":
        message = changed_message(case, size=0)
        case = (*case[:4], message, *case[5:])
        case[3].iter_content.return_value = iter([b"mo", b"vie-more"])
    elif kind == "truncated":
        case[3].iter_content.return_value = iter([b"mo"])
    else:

        def broken() -> Iterator[bytes]:
            yield b"mo"
            raise ReadTimeout("https://signed-secret.invalid")

        case[3].iter_content.return_value = broken()
    result = invoke(case, output)
    assert result.exit_code != 0
    assert "signed-secret" not in result.stdout
    assert not list(output.parent.iterdir())
    case[3].close.assert_called_once()


@pytest.mark.parametrize("size", [None, 0])
def test_unknown_metadata_size_still_uses_response_length(
    case: Case, size: Any
) -> None:
    """Old zero-sized placeholders are not interpreted as zero-byte files."""
    assert consume(case, changed_message(case, size=size)) == b"movie"


def test_missing_lengths_still_streams_under_hard_budget(case: Case) -> None:
    """A byte ceiling applies even when both metadata and headers omit the size."""
    case[3].headers = {"Content-Type": "application/octet-stream"}
    message = changed_message(case, size=None, content_type="application/octet-stream")
    assert consume(case, message, max_bytes=5) == b"movie"


@pytest.mark.parametrize("when", [0, 1, 2])
def test_mailbox_recreation_prevents_publication(
    case: Case, output: Path, when: int
) -> None:
    """Check namespace before retrieval, before bytes, and before file publication."""
    folder = case[4].folder
    case[5].side_effect = [[folder]] * (1 + when) + [
        [folder.model_copy(update={"uid_validity": 99})]
    ]
    result = invoke(case, output)
    assert result.exit_code != 0
    assert not list(output.parent.iterdir())


def test_message_scope_mismatch_fails_before_http(case: Case) -> None:
    """Do not resolve another mailbox's local UID with this message context."""
    message = case[4].model_copy(
        update={"mailbox": case[4].mailbox.model_copy(update={"id": "other"})}
    )
    with pytest.raises(ValueError, match="identities"):
        consume(case, message)
    case[6].assert_not_called()


def test_partial_consumer_closes_response(case: Case) -> None:
    """A context-managed partial read closes both response and isolated session."""
    with case[0].mail_drop_attachment(case[4], 2) as chunks:
        assert next(chunks) == b"mo"
    case[3].close.assert_called_once()


def test_private_capabilities_survive_binding_but_not_json(case: Case) -> None:
    """Metadata rendering/export must not leak signed cloud-part identifiers."""
    service, owned, _, _, message, _, _ = case
    response = MagicMock(spec=Response)
    response.status_code = 200
    response.json.return_value = load_fixture("mail", "maildrop_message_response.json")
    owned.post.return_value = response
    selected = service.message(message.folder, message.id)
    assert selected is not None
    assert selected.parts[1].mail_drop_url == message.parts[1].mail_drop_url
    assert selected.parts[1].is_mail_drop is True
    payload = selected.model_dump_json()
    assert "cvws" not in payload
    assert "s=fake" not in payload
    assert "cloudpart:<redacted>" in payload
    assert "cvws" not in repr(selected.parts[1])
    assert message.parts[0].model_dump(mode="json")["id"] == "1"


def test_help_and_invalid_options_are_offline() -> None:
    """Require explicit part and destination without opening an account for help."""
    with patch.object(CLIState, "get_api") as get_api:
        help_result = CliRunner().invoke(app, ["mail", "download-maildrop", "--help"])
        invalid = CliRunner().invoke(
            app, ["mail", "download-maildrop", "message", "--folder-id", "10"]
        )
    assert help_result.exit_code == 0
    assert invalid.exit_code != 0
    get_api.assert_not_called()


def test_existing_destination_is_rejected_before_auth(output: Path) -> None:
    """Do not authenticate or overwrite an existing Mail Drop destination."""
    output.write_bytes(b"keep")
    with patch.object(CLIState, "get_api") as get_api:
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "download-maildrop",
                "message",
                "--folder-id",
                "10",
                "--part-index",
                "2",
                "--output",
                str(output),
            ],
        )
    assert result.exit_code != 0
    assert output.read_bytes() == b"keep"
    get_api.assert_not_called()


def test_connection_failure_does_not_print_signed_url(case: Case, output: Path) -> None:
    """Transport diagnostics must not expose the capability URL to CLI output."""
    case[6].side_effect = ReadTimeout("https://signed-secret.invalid")
    result = invoke(case, output)
    assert result.exit_code != 0
    assert "signed-secret" not in result.stdout
    assert not list(output.parent.iterdir())


def test_missing_uid_validity_advertises_refresh_not_limit(
    case: Case, output: Path
) -> None:
    """download-maildrop has no --limit option, so never direct users to it."""
    unvalidated = case[4].folder.model_copy(update={"uid_validity": None})
    case[5].return_value = [unvalidated]
    api = MagicMock()
    api.account_name = "user@example.test"
    api.mail = case[0]
    with patch.object(CLIState, "get_api", return_value=api):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "download-maildrop",
                case[4].id,
                "--folder-id",
                "10",
                "--part-index",
                "2",
                "--output",
                str(output),
                "--format",
                "json",
            ],
        )
    assert result.exit_code != 0
    assert result.exception is not None
    failure = result.exception.args[0]
    assert "--limit" not in failure
    assert "icloud mail folders" in failure
    assert not list(output.parent.iterdir())


def test_caller_exceptions_are_not_reclassified(case: Case) -> None:
    """A RequestException raised by the caller inside the block stays unchanged."""

    def exercise() -> None:
        with case[0].mail_drop_attachment(case[4], 2) as chunks:
            assert next(chunks) == b"mo"
            raise ReadTimeout("caller-owned")

    with pytest.raises(ReadTimeout, match="caller-owned"):
        exercise()


@pytest.mark.parametrize("phase", ["send", "stream"])
def test_transport_failures_name_the_class_without_the_url(
    case: Case, phase: str
) -> None:
    """The message carries the failure class; the signed URL stays chained only."""
    if phase == "send":
        case[6].side_effect = ReadTimeout("https://signed-secret.invalid")
    else:

        def broken() -> Iterator[bytes]:
            yield b"mo"
            raise ReadTimeout("https://signed-secret.invalid")

        case[3].iter_content.return_value = broken()
    with pytest.raises(MailError) as caught:
        consume(case)
    assert "(ReadTimeout)" in str(caught.value)
    assert "signed-secret" not in str(caught.value)
    assert isinstance(caught.value.__cause__, ReadTimeout)
