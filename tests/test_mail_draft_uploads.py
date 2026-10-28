"""Ordinary cached uploads through the real Mail service and mounted CLI."""
# cspell:words cachedpart

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from tempfile import gettempdir
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from requests import ConnectionError as RequestsConnectionError
from requests import Response
from typer.testing import CliRunner

from pyicloud.cli.app import app
from pyicloud.cli.context import CLIAbort, CLIState
from pyicloud.exceptions import PyiCloudException
from pyicloud.services.mail import MailService, MailWriteUncertain
from pyicloud.services.mail._draft_uploads import (
    UPLOAD_TIMEOUT,
    checked_part_url,
)
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture

UPLOAD_ID = "11111111-2222-4333-8444-555555555555"
PART_ID = "cachedpart:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
ROOT = "https://gateway.example.test"
DATA = b"Synthetic test data."
INPUT_PATH = str(Path(gettempdir()) / "python-test-results" / "synthetic.txt")


@pytest.fixture(name="upload")
def upload_case() -> tuple[MailService, MagicMock, MagicMock]:
    """Only the network transport is mocked; upload validation remains real."""
    session = MagicMock(spec=PyiCloudSession)
    response = MagicMock(spec=Response)
    response.status_code = 200
    payload = load_fixture("mail", "draft_attachment_upload_response.json")
    payload["messagePart"]["size"] = len(DATA)
    response.json.return_value = payload
    session.request_raw.return_value = response
    service = MailService(ROOT, session, {"dsid": "12345", "clientIntent": "stale"})
    return service, session, response


def submit(service: MailService) -> Any:
    """Use one explicit synthetic byte payload and filename throughout the cases."""
    with patch(
        "pyicloud.services.mail._draft_uploads.uuid4", return_value=UUID(UPLOAD_ID)
    ):
        return service.upload_draft_attachment(
            DATA, filename="synthetic.txt", content_type="text/plain"
        )


def test_upload_wire_and_bound_handle(upload: tuple[Any, ...]) -> None:
    """Raw bytes, bounded timeout, no redirects, no stale intent and no save/send."""
    service, session, response = upload
    result = submit(service)
    session.request_raw.assert_called_once_with(
        "POST",
        ROOT + "/mailws2/v1/message/part",
        params={
            "dsid": "12345",
            "X-id": UPLOAD_ID,
            "X-type": "text/plain",
            "X-size": str(len(DATA)),
            "X-name": "synthetic.txt",
        },
        data=DATA,
        headers={"Content-Type": "text/plain"},
        timeout=UPLOAD_TIMEOUT,
        allow_redirects=False,
    )
    assert service.params["clientIntent"] == "stale"
    assert result.account_id == "12345"
    assert result.service_root == ROOT
    assert result.reference.guid == result.reference.content_id == PART_ID
    assert result.reference.size == len(DATA)
    assert result.reference.url.startswith(ROOT + "/mailws2/v1/message/part?")
    assert result.reference.mail_drop_expiration_date is None
    response.close.assert_called_once()
    session.post.assert_not_called()
    session.get.assert_not_called()


@pytest.mark.parametrize(
    "data,name,mime",
    [
        ("text", "synthetic.txt", "text/plain"),
        (bytearray(b"x"), "synthetic.txt", "text/plain"),
        (DATA, "", "text/plain"),
        (DATA, ".", "text/plain"),
        (DATA, "..", "text/plain"),
        (DATA, "../x.txt", "text/plain"),
        (DATA, "a\\b", "text/plain"),
        (DATA, "a\x7f", "text/plain"),
        (DATA, "a\r\nb", "text/plain"),
        (DATA, "\ud800", "text/plain"),
        (DATA, None, "text/plain"),
        (DATA, "synthetic.txt", "text/plain; charset=utf-8"),
        (DATA, "synthetic.txt", "text/plain\r\nX-Evil: yes"),
        (DATA, "synthetic.txt", None),
    ],
)
def test_bad_input_never_dispatches(
    upload: tuple[Any, ...], data: Any, name: Any, mime: Any
) -> None:
    """Input validation precedes every network call, including account reads."""
    service, session, _ = upload
    with pytest.raises(ValueError):
        service.upload_draft_attachment(data, filename=name, content_type=mime)
    session.request_raw.assert_not_called()
    session.post.assert_not_called()
    session.get.assert_not_called()


def test_size_limit_before_dispatch(upload: tuple[Any, ...]) -> None:
    """Exercise a tiny patched budget rather than allocating a large test file."""
    service, session, _ = upload
    with (
        patch("pyicloud.services.mail._draft_uploads.MAX_DRAFT_UPLOAD_BYTES", 2),
        pytest.raises(ValueError, match="25 MiB"),
    ):
        submit(service)
    session.request_raw.assert_not_called()


@pytest.mark.parametrize("account", [None, "", " ", 12345])
def test_upload_requires_discovered_account(
    upload: tuple[Any, ...], account: Any
) -> None:
    """No anonymous upload or account-ID coercion is allowed."""
    service, session, _ = upload
    service.params["dsid"] = account
    with pytest.raises(ValueError, match="account"):
        submit(service)
    session.request_raw.assert_not_called()


@pytest.mark.parametrize("status", [207, 302, 401, 403, 429, 500])
def test_non_success_never_retries(upload: tuple[Any, ...], status: int) -> None:
    """Even authentication failures cannot replay an already submitted upload."""
    service, session, response = upload
    response.status_code = status
    with pytest.raises(MailWriteUncertain, match="cached upload may exist"):
        submit(service)
    session.request_raw.assert_called_once()
    response.json.assert_not_called()
    response.close.assert_called_once()


@pytest.mark.parametrize(
    "stage,error",
    [
        ("dispatch", RequestsConnectionError),
        ("dispatch", OSError),
        ("dispatch", PyiCloudException),
        ("json", RequestsConnectionError),
        ("json", OSError),
        ("json", PyiCloudException),
        ("close", RequestsConnectionError),
        ("close", OSError),
    ],
)
def test_transport_errors_are_uncertain(
    upload: tuple[Any, ...], stage: str, error: type[Exception]
) -> None:
    """Do not promise that cached state is absent after an interrupted write."""
    service, session, response = upload
    target = session.request_raw if stage == "dispatch" else getattr(response, stage)
    target.side_effect = error("synthetic failure")
    with pytest.raises(MailWriteUncertain, match="uncertain"):
        submit(service)
    session.request_raw.assert_called_once()
    assert response.close.call_count == (0 if stage == "dispatch" else 1)


@pytest.mark.parametrize(
    "key,value",
    [
        ("uid", "99999999-2222-4333-8444-555555555555"),
        ("name", "other.txt"),
        ("size", 999),
        ("datatype", "image/jpeg"),
        ("encoding", "binary"),
        ("guid", "messagepart:Drafts/100-1"),
        ("url", "https://attacker.example.test/steal"),
    ],
)
def test_response_metadata_must_match_submission(
    upload: tuple[Any, ...], key: str, value: Any
) -> None:
    """A 200 response is not enough to mint a reusable cached attachment handle."""
    service, session, response = upload
    payload = deepcopy(response.json.return_value)
    (payload if key == "uid" else payload["messagePart"])[key] = value
    response.json.return_value = payload
    with pytest.raises(MailWriteUncertain):
        submit(service)
    session.request_raw.assert_called_once()
    response.close.assert_called_once()


@pytest.mark.parametrize("payload", [None, {}, {"uid": UPLOAD_ID, "msgPart": {}}, []])
def test_malformed_upload_envelope(upload: tuple[Any, ...], payload: Any) -> None:
    """Legacy or missing fields cannot be treated as successful modern uploads."""
    service, session, response = upload
    response.json.return_value = payload
    with pytest.raises(MailWriteUncertain):
        submit(service)
    session.request_raw.assert_called_once()


def test_invalid_json_closes_response(upload: tuple[Any, ...]) -> None:
    """A non-JSON acknowledgement remains uncertain, with no leaked response."""
    service, _, response = upload
    response.json.side_effect = ValueError("not JSON")
    with pytest.raises(MailWriteUncertain):
        submit(service)
    response.close.assert_called_once()


@pytest.mark.parametrize(
    "url",
    [
        "//attacker.example.test/mailws2/v1/message/part",
        "http://gateway.example.test/mailws2/v1/message/part",
        "https://gateway.example.test:444/mailws2/v1/message/part",
        "https://user:password@gateway.example.test/mailws2/v1/message/part",
        "/mailws2/v1/message/part#fragment",
        "/mailws2/v1/message/part?guid=wrong",
        "/mailws2/v1/message/part?guid=one&guid=two",
        "/mailws2/v1/message/part?invalid",
        "/mailws2/v1/message/part?name=synthetic.txt&extra=true",
        "/mailws2/v1/message/../part",
        "/mailws2/v1/message/part\r\n",
        "https://gateway.example.test:invalid/mailws2/v1/message/part",
    ],
)
def test_part_url_is_not_an_arbitrary_fetch_instruction(url: str) -> None:
    """Reject wrong origins, duplicate selectors, credentials and path tricks."""
    with pytest.raises((PyiCloudException, ValueError)):
        checked_part_url(ROOT, url, PART_ID, "synthetic.txt", "text/plain")


def test_absolute_default_port_part_url_is_canonicalized() -> None:
    """An explicit HTTPS default port must not require a different trusted origin."""
    part = load_fixture("mail", "draft_attachment_upload_response.json")["messagePart"]
    result = checked_part_url(
        ROOT, ROOT + ":443" + part["url"], PART_ID, "synthetic.txt", "text/plain"
    )
    assert result.startswith(ROOT + "/mailws2/v1/message/part?")
    assert ":443" not in result


@pytest.mark.parametrize("mode", ["json", "text"])
def test_upload_cli_uses_real_service(upload: tuple[Any, ...], mode: str) -> None:
    """CLI uploads bytes without echoing content or silently saving a draft."""
    service, session, _ = upload
    api = MagicMock(mail=service, account_name="login@example.test")
    with (
        patch.object(Path, "open", return_value=BytesIO(DATA)) as source,
        patch.object(CLIState, "get_api", return_value=api),
        patch.object(CLIState, "open"),
        patch(
            "pyicloud.services.mail._draft_uploads.uuid4", return_value=UUID(UPLOAD_ID)
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "upload-draft-attachment",
                "--file",
                INPUT_PATH,
                "--content-type",
                "text/plain",
                "--format",
                mode,
            ],
        )
    assert result.exit_code == 0, result.exception
    assert DATA.decode() not in result.stdout
    source.assert_called_once_with("rb")
    session.request_raw.assert_called_once()
    if mode == "json":
        assert json.loads(result.stdout)["reference"]["guid"] == PART_ID
    else:
        assert "Not Sent" in result.stdout


@pytest.mark.parametrize("kind", ["missing", "size", "mime"])
def test_upload_cli_errors_before_authentication(
    upload: tuple[Any, ...], kind: str
) -> None:
    """Local input failures must not authenticate or trigger an upload."""
    _, session, _ = upload
    with (
        patch.object(Path, "open", return_value=BytesIO(DATA)) as source,
        patch.object(CLIState, "get_api") as get_api,
        patch.object(CLIState, "open"),
        patch(
            "pyicloud.cli.commands.mail.MAX_DRAFT_UPLOAD_BYTES",
            2 if kind == "size" else 100,
        ),
        patch(
            "pyicloud.services.mail._draft_uploads.MAX_DRAFT_UPLOAD_BYTES",
            2 if kind == "size" else 100,
        ),
    ):
        if kind == "missing":
            source.side_effect = OSError("missing")
        result = CliRunner().invoke(
            app,
            [
                "mail",
                "upload-draft-attachment",
                "--file",
                INPUT_PATH,
                "--content-type",
                "invalid" if kind == "mime" else "text/plain",
            ],
        )
    assert isinstance(result.exception, CLIAbort)
    get_api.assert_not_called()
    session.request_raw.assert_not_called()
