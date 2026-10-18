"""Offline tests of bounded, non-mutating Mail reads."""
# pylint: disable=protected-access

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

from pydantic import ValidationError
import pytest
from requests import RequestException, Response

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudConnectionException,
    PyiCloudException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud.services.mail import (
    MailError,
    MailFolder,
    MailMessage,
    MailPart,
    MailService,
)
from pyicloud.services.mail.models import MailBodyRequest, MessageResponse
from pyicloud.session import PyiCloudSession
from tests.fixture_loader import load_fixture


@pytest.fixture(name="session")
def mail_session_fixture() -> MagicMock:
    """A session whose HTTP responses are entirely local."""
    return MagicMock(spec=PyiCloudSession)


@pytest.fixture(name="service")
def mail_service_fixture(session: MagicMock) -> MailService:
    """A discovered endpoint with fake client parameters."""
    return MailService("https://example.test", session, {"dsid": "fake-account"})


@pytest.fixture(name="folder")
def mail_folder_fixture() -> MailFolder:
    """A synthetic inbox."""
    return MailFolder(identifier="10", name="INBOX", uidValidity=1234)


@pytest.fixture(name="message")
def mail_message_fixture(folder: MailFolder) -> MailMessage:
    """A message with advertised plain, HTML, and attachment parts."""
    record = MessageResponse.model_validate(
        load_fixture("mail", "messages_response.json")
    ).records[0]
    return MailMessage(**record.model_dump(), folder=folder)


def response(data: Any, *, status: int = 200) -> MagicMock:
    """An HTTP response with a JSON body."""
    result = MagicMock(spec=Response)
    result.status_code = status
    result.json.return_value = data
    result.headers = {"Content-Type": "application/pdf"}
    result.iter_content.return_value = iter([b"%PDF-", b"", b"synthetic\n%%EOF"])
    return result


def test_folders(service: MailService, session: MagicMock) -> None:
    """Folder queries use the session and require a typed envelope."""
    result = response(load_fixture("mail", "folders_response.json"))
    session.post.return_value = result
    folders = service.folders()
    assert [f.id for f in folders] == ["10", "20"]
    assert folders[1].message_count == 356
    call = session.post.call_args
    assert call.args[0] == "https://example.test/mailws2/v1/geqs/query"
    assert call.kwargs["params"] == {"dsid": "fake-account"}
    assert call.kwargs["json"]["predicate"]["value"] is False
    assert call.kwargs["json"]["includeLabels"] is True
    assert call.kwargs["timeout"] == (10, 60)
    assert call.kwargs["allow_redirects"] is False
    result.close.assert_called_once()


def test_messages(service: MailService, session: MagicMock, folder: MailFolder) -> None:
    """Repeated records are deduplicated, and snapshots never promise completeness."""
    data = load_fixture("mail", "messages_response.json")
    data["domainObjects"] *= 2
    session.post.return_value = response(data)
    page = service.messages(folder, limit=5)
    assert len(page.messages) == 1
    assert page.messages[0].folder == folder
    assert page.messages[0].uid == 7
    assert page.messages[0].internal_date == 1700000000.125
    assert page.complete is False
    assert page.limit == 5
    query = session.post.call_args.kwargs["json"]
    assert query["predicate"]["value"] == folder.id
    assert query["orderby"]["ascending"] is False
    assert query["limit"] == 5
    assert session.post.call_count == 1


@pytest.mark.parametrize("limit", [0, -1, 1001, True, 1.5, "10"])
def test_invalid_limits(
    service: MailService, session: MagicMock, folder: MailFolder, limit: Any
) -> None:
    """Invalid limits fail before HTTP for both listing and search."""
    with pytest.raises(ValueError):
        service.messages(folder, limit=limit)
    with pytest.raises(ValueError):
        service.search(folder, "test", limit=limit)
    session.post.assert_not_called()


@pytest.mark.parametrize("method", ["messages", "search"])
def test_empty_results(
    service: MailService, session: MagicMock, folder: MailFolder, method: str
) -> None:
    """An empty list is a valid bounded result, not an error."""
    session.post.return_value = response({"domainObjects": []})
    if method == "messages":
        page = service.messages(folder)
        assert not page.messages
        assert page.complete is False
    else:
        search_page = service.search(folder, "none")
        assert not search_page.results
        assert search_page.complete is False


@pytest.mark.parametrize("method", ["messages", "search"])
def test_excess_results(
    service: MailService, session: MagicMock, folder: MailFolder, method: str
) -> None:
    """A server ignoring the limit is surfaced, not silently truncated."""
    filename = (
        "messages_response.json" if method == "messages" else "search_response.json"
    )
    data = load_fixture("mail", filename)
    data["domainObjects"] *= 2
    session.post.return_value = response(data)
    with pytest.raises(MailError, match="exceeded"):
        if method == "messages":
            service.messages(folder, limit=1)
        else:
            service.search(folder, "test", limit=1)


def test_wrong_folder(service: MailService, session: MagicMock) -> None:
    """Do not bind one folder's UID to another folder."""
    session.post.return_value = response(load_fixture("mail", "messages_response.json"))
    with pytest.raises(MailError, match="another folder"):
        service.messages(MailFolder(identifier="wrong", name="Other"))


@pytest.mark.parametrize("field", ["all", "subject", "from", "to", "cc", "body"])
def test_search(
    service: MailService, session: MagicMock, folder: MailFolder, field: Any
) -> None:
    """Search preserves scoped text predicates and thread grouping."""
    session.post.return_value = response(load_fixture("mail", "search_response.json"))
    page = service.search(folder, 'a"b', field=field)
    assert page.results[0].count == 2
    assert page.complete is False
    q = session.post.call_args.kwargs["json"]
    assert q["collapseOn"] == "appleThrid"
    assert q["predicate"]["value"] == folder.id
    term = q["predicate"]["and"][1]
    assert term["value"] == 'a"b'
    if field == "all":
        assert [
            term["expression"]["value"],
            *[p["expression"]["value"] for p in term["or"]],
        ] == ["subject", "to", "from", "cc", "BODY"]
    else:
        assert "or" not in term
        assert term["expression"]["value"] == ("BODY" if field == "body" else field)


@pytest.mark.parametrize(
    "text,field", [("", "all"), ("  ", "all"), (None, "all"), ("ok", "bad")]
)
def test_invalid_search(
    service: MailService, session: MagicMock, folder: MailFolder, text: Any, field: Any
) -> None:
    """Invalid search inputs never hit the endpoint."""
    with pytest.raises(ValueError):
        service.search(folder, text, field=field)
    session.post.assert_not_called()


def test_body(service: MailService, session: MagicMock, message: MailMessage) -> None:
    """Body selection is explicit and does not send the browser's seen mutation."""
    session.post.return_value = response(load_fixture("mail", "body_response.json"))
    result = service.body(message)
    assert result.parts[0].content == "Synthetic plain text."
    assert "Synthetic" not in repr(result.parts[0])
    assert "Synthetic" not in repr(result)
    query = session.post.call_args.kwargs["json"]
    assert query["parts"] == ["1.1", "1.2"]
    assert query["dontMarkAsRead"] is True
    assert query["sessionHeaders"]["modseq"] is None
    assert query["sessionHeaders"]["folder"] == "INBOX"
    assert query["uid"] == "7"
    assert session.post.call_count == 1
    assert session.post.call_args.args[0].endswith("/message/get")


def test_no_body(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Unsupported or attachment-only messages do not trigger a body request."""
    with pytest.raises(ValueError, match="text body"):
        service.body(message.model_copy(update={"parts": []}))
    session.post.assert_not_called()


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {"domainObjects": None},
        {"domainObjects": [{"identifier": "x"}]},
        {"error": "synthetic"},
        {"errors": ["synthetic"]},
    ],
)
def test_bad_envelopes(service: MailService, session: MagicMock, data: Any) -> None:
    """Bad shapes and in-band errors are not successful empty mailboxes."""
    result = response(data)
    session.post.return_value = result
    with pytest.raises(MailError) as caught:
        service.folders()
    assert isinstance(caught.value, PyiCloudException)
    result.close.assert_called_once()


def test_bad_json(service: MailService, session: MagicMock) -> None:
    """JSON decoding failures preserve their cause and close the response."""
    result = response(None)
    result.json.side_effect = ValueError("synthetic malformed JSON")
    session.post.return_value = result
    with pytest.raises(MailError, match="invalid JSON") as caught:
        service.folders()
    assert isinstance(caught.value.__cause__, ValueError)
    result.close.assert_called_once()


def test_http_error(service: MailService, session: MagicMock) -> None:
    """Reject HTTP errors and redirects even if the underlying session returns them."""
    result = response({}, status=302)
    session.post.return_value = result
    with pytest.raises(MailError, match="HTTP 302"):
        service.folders()
    result.close.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [PyiCloudConnectionException("offline"), PyiCloudAPIResponseException("auth", 403)],
)
def test_session_errors(
    service: MailService, session: MagicMock, error: Exception
) -> None:
    """Preserve existing authentication and transport exception contracts."""
    session.post.side_effect = error
    with pytest.raises(type(error)):
        service.folders()


def test_attachment(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Download bytes, not encoded-size bytes or a path from a remote filename."""
    result = response(None)
    session.get.return_value = result
    with service.attachment(message, "2") as chunks:
        assert b"".join(chunks) == b"%PDF-synthetic\n%%EOF"
    query = session.get.call_args.kwargs
    assert query["params"] == {
        "guid": "messagepart:INBOX/7-2",
        "type": "application/pdf",
        "name": "example.pdf",
        "size": 112008,
    }
    assert query["stream"] is True
    assert query["allow_redirects"] is False
    assert query["timeout"] == (10, 60)
    result.close.assert_called_once()


def test_attachment_early_exit(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Context exit closes a partially consumed response, including caller errors."""
    result = response(None)
    session.get.return_value = result
    with pytest.raises(RuntimeError), service.attachment(message, "2") as chunks:
        assert next(chunks) == b"%PDF-"
        raise RuntimeError("caller stopped")
    result.close.assert_called_once()


def test_attachment_optional_metadata(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Filename and encoded size are optional and never filesystem destinations."""
    part = MailPart(partId="2", attach=True)
    result = response(None)
    result.headers = {"Content-Type": "application/octet-stream"}
    session.get.return_value = result
    with service.attachment(
        message.model_copy(update={"parts": [part]}), "2"
    ) as chunks:
        list(chunks)
    assert set(session.get.call_args.kwargs["params"]) == {"guid", "type"}


@pytest.mark.parametrize("content_type", ["application/pdf", "application/pdf; x=y"])
def test_attachment_absent_type_metadata_accepts_the_real_type(
    service: MailService, session: MagicMock, message: MailMessage, content_type: str
) -> None:
    """With no advertised type there is nothing to match, only pages to reject."""
    part = MailPart(partId="2", attach=True)
    result = response(None)
    result.headers = {"Content-Type": content_type}
    session.get.return_value = result
    with service.attachment(
        message.model_copy(update={"parts": [part]}), "2"
    ) as chunks:
        assert b"".join(chunks) == b"%PDF-synthetic\n%%EOF"


@pytest.mark.parametrize("content_type", ["", "text/html"])
def test_attachment_absent_type_metadata_still_rejects_pages(
    service: MailService, session: MagicMock, message: MailMessage, content_type: str
) -> None:
    """An empty type or an HTML page is an error response, not attachment bytes."""
    part = MailPart(partId="2", attach=True)
    result = response(None)
    result.headers = {"Content-Type": content_type}
    session.get.return_value = result
    with (
        pytest.raises(MailError),
        service.attachment(message.model_copy(update={"parts": [part]}), "2"),
    ):
        pytest.fail("must fail before yielding")


@pytest.mark.parametrize(
    "status,content_type",
    [(302, "application/pdf"), (403, "application/pdf"), (200, "text/html"), (200, "")],
)
def test_attachment_failures(
    service: MailService,
    session: MagicMock,
    message: MailMessage,
    status: int,
    content_type: str,
) -> None:
    """Auth pages and redirects are not returned as PDF content."""
    result = response(None, status=status)
    result.headers = {"Content-Type": content_type}
    session.get.return_value = result
    with pytest.raises(MailError), service.attachment(message, "2"):
        pytest.fail("must fail before yielding")
    result.close.assert_called_once()


def test_unknown_attachment(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Only an advertised attachment part is accepted."""
    with pytest.raises(ValueError), service.attachment(message, "../2"):
        pytest.fail("invalid part")
    session.get.assert_not_called()


def test_stream_failure(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """Mid-stream transport errors are translated and responses always close."""

    def failed_chunks() -> Iterator[bytes]:
        yield b"first"
        raise RequestException("synthetic truncated response")

    result = response(None)
    result.iter_content.return_value = failed_chunks()
    session.get.return_value = result
    with (
        pytest.raises(MailError, match="stream failed") as caught,
        service.attachment(message, "2") as chunks,
    ):
        list(chunks)
    assert isinstance(caught.value.__cause__, RequestException)
    result.close.assert_called_once()


@pytest.mark.parametrize(
    "root",
    [
        "http://example.test",
        "https://user:pass@example.test",
        "https://example.test/?q=1",
        "https://example.test/#x",
        "https://",
    ],
)
def test_invalid_endpoint(session: MagicMock, root: str) -> None:
    """Never send authenticated requests to arbitrary insecure roots."""
    with pytest.raises(ValueError):
        MailService(root, session, {})


def test_model_validation() -> None:
    """MIME part IDs cannot become arbitrary download selectors."""
    with pytest.raises(ValidationError):
        MailBodyRequest.model_validate({
            "uid": "7",
            "parts": ["../2"],
            "session_headers": {"folder": "INBOX"},
        })


@pytest.mark.parametrize(
    "part_id", ["../2", "cloudpart:https://example.test/attachment?token=synthetic"]
)
def test_external_attachment_metadata(
    service: MailService, session: MagicMock, folder: MailFolder, part_id: str
) -> None:
    """Opaque part metadata is listable, never followed as a download URL."""
    data = load_fixture("mail", "messages_response.json")
    data["domainObjects"][0]["parts"][2]["partId"] = part_id
    session.post.return_value = response(data)
    message = service.messages(folder).messages[0]
    assert message.parts[2].id == part_id
    assert part_id not in repr(message.parts[2])
    with (
        pytest.raises(ValueError, match="not supported"),
        service.attachment(message, part_id),
    ):
        pytest.fail("must not follow opaque identifiers")
    session.get.assert_not_called()


def test_external_body_metadata(
    service: MailService, session: MagicMock, message: MailMessage
) -> None:
    """An external part masquerading as a text body is not requested."""
    part = MailPart(
        partId="cloudpart:https://example.test/body", contentType="text/plain"
    )
    with pytest.raises(ValueError, match="no supported"):
        service.body(message.model_copy(update={"parts": [part]}))
    session.post.assert_not_called()


def test_folder_continuation(service: MailService, session: MagicMock) -> None:
    """Do not quietly advertise a partial folder list as complete."""
    session.post.return_value = response({
        "domainObjects": [],
        "continuationMarker": "synthetic-marker",
    })
    with pytest.raises(MailError, match="unsupported continuation"):
        service.folders()


@pytest.mark.parametrize(
    "error",
    [PyiCloudConnectionException("offline"), PyiCloudAPIResponseException("auth", 403)],
)
def test_attachment_session_error(
    service: MailService, session: MagicMock, message: MailMessage, error: Exception
) -> None:
    """Failures before a download response retain session error types."""
    session.get.side_effect = error
    with pytest.raises(type(error)), service.attachment(message, "2"):
        pytest.fail("no response was received")


def test_mail_property(pyicloud_service: PyiCloudService) -> None:
    """The property discovers the new endpoint once and caches the service."""
    with patch.object(
        pyicloud_service, "get_webservice_url", return_value="https://example.test"
    ) as discover:
        mail = pyicloud_service.mail
        assert isinstance(mail, MailService)
        assert pyicloud_service.mail is mail
        discover.assert_called_once_with("mccgateway")


def test_missing_mail_service(pyicloud_service: PyiCloudService) -> None:
    """Missing Mail activation stays a standard service-availability exception."""
    with (
        patch.object(
            pyicloud_service,
            "get_webservice_url",
            side_effect=PyiCloudServiceNotActivatedException("missing"),
        ),
        pytest.raises(PyiCloudServiceNotActivatedException),
    ):
        _ = pyicloud_service.mail
