"""Direct adapter dispatch without custom session replay or response hooks."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast
from urllib.parse import urlsplit

from requests import Request, Response, Session
from requests.adapters import HTTPAdapter
from requests.cookies import extract_cookies_to_jar
from requests.sessions import merge_setting

from pyicloud.services.drive import DriveService

from .authorization import WriteDenied
from .context import BoundedAdapter


def checked_adapter(session: Any, url: str, *, bounded: bool = False) -> HTTPAdapter:
    """Validate the actual longest-prefix adapter before spending resources."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise WriteDenied("insecure_mutation_route") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise WriteDenied("insecure_mutation_route")
    try:
        adapter = session.get_adapter(url)
    except Exception:
        raise WriteDenied("mutation_retry_policy_unavailable") from None
    if (
        type(adapter) not in {HTTPAdapter, BoundedAdapter}
        or adapter.max_retries.total != 0
    ):
        raise WriteDenied("mutation_retry_policy_unavailable")
    if not isinstance(session, Session):
        raise WriteDenied("mutation_session_policy_unavailable")
    if session.auth is not None:
        raise WriteDenied("mutation_session_policy_unavailable")
    if bounded and not isinstance(adapter, BoundedAdapter):
        raise WriteDenied("upload_deadline_policy_unavailable")
    return cast(HTTPAdapter, adapter)


def prepared_url(session: Session, url: str, params: Any = None) -> str:
    """Mirror Requests query-default precedence without invoking session hooks."""
    merge = cast(Callable[[Any, Any], Any], merge_setting)
    merged = merge(params, session.params)
    return Request("POST", url, params=merged).prepare().url or ""


def request_once(session: Session, method: str, url: str, **kwargs: Any) -> Response:
    """Prepare ordinary session cookies, but bypass request/send/hooks/recovery."""
    kwargs.pop("allow_redirects", None)
    stream = kwargs.pop("stream", True)
    timeout = kwargs.pop("timeout", (5.0, 30.0))
    kwargs["headers"] = {**session.headers, **(kwargs.pop("headers", None) or {})}
    kwargs["cookies"] = session.cookies.copy()
    merge = cast(Callable[[Any, Any], Any], merge_setting)
    kwargs["params"] = merge(kwargs.pop("params", None), session.params)
    kwargs.pop("auth", None)
    kwargs.pop("hooks", None)
    request = Request(method, url, **kwargs).prepare()
    adapter = checked_adapter(session, request.url or url)
    request.hooks = {"response": []}
    send = cast(
        Callable[..., Response],
        BoundedAdapter.send
        if isinstance(adapter, BoundedAdapter)
        else HTTPAdapter.send,
    )
    response = send(
        adapter,
        request,
        stream=stream,
        timeout=timeout,
        verify=session.verify,
        cert=session.cert,
        proxies={},
    )
    store_cookies = cast(Callable[[Any, Any, Any], None], extract_cookies_to_jar)
    store_cookies(session.cookies, request, response.raw)
    return response


class _MutationSession:
    """Expose only the core service's POST operation through direct dispatch."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def post(self, url: str, **kwargs: Any) -> Response:
        """Dispatch the library's unchanged POST payload exactly once."""
        return request_once(self.session, "POST", url, **kwargs)


@contextmanager
def mutation_session(drive: Any) -> Iterator[Any]:
    """Keep existing service payload construction, replace only its dispatch path."""
    previous = drive.session
    if isinstance(drive, DriveService):
        yield DriveService(
            drive.service_root,
            drive.document_root,
            cast(Any, _MutationSession(previous)),
            drive.params,
        )
        return
    drive.session = _MutationSession(previous)
    try:
        yield drive
    finally:
        drive.session = previous
