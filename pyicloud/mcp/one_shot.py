"""Direct adapter dispatch without custom session replay or response hooks."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any, cast
from urllib.parse import urlsplit

from requests import Request, Response, Session
from requests.adapters import HTTPAdapter
from requests.cookies import extract_cookies_to_jar
from requests.sessions import merge_setting
from requests.utils import prepend_scheme_if_needed, select_proxy

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


def validate_dispatch_policy(session: Session, url: str) -> None:
    """Validate pinned dispatch without consulting a second adapter selection."""
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
    if not isinstance(session, Session) or session.auth is not None:
        raise WriteDenied("mutation_session_policy_unavailable")


def prepared_url(session: Session, url: str, params: Any = None) -> str:
    """Mirror Requests query-default precedence without invoking session hooks."""
    merge = cast(Callable[[Any, Any], Any], merge_setting)
    merged = merge(params, session.params)
    return Request("POST", url, params=merged).prepare().url or ""


def transport_settings(
    session: Session, url: str, stream: Any = True
) -> dict[str, Any]:
    """Resolve trusted CA/proxy settings without netrc, hooks or session replay."""
    settings = Session.merge_environment_settings(
        session, url, {}, stream, session.verify, session.cert
    )
    selected = select_proxy(url, settings["proxies"])
    normalize = cast(Callable[[str, str], str], prepend_scheme_if_needed)
    if selected and urlsplit(normalize(selected, "http")).scheme not in {
        "http",
        "https",
    }:
        raise WriteDenied("unsupported_proxy")
    return dict(settings)


def request_once(
    session: Session,
    method: str,
    url: str,
    *,
    adapter: BoundedAdapter | None = None,
    **kwargs: Any,
) -> Response:
    """Prepare ordinary session cookies, but bypass request/send/hooks/recovery."""
    validate_dispatch_policy(session, url)
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
    selected_adapter = (
        adapter
        if adapter is not None
        else checked_adapter(session, request.url or url, bounded=True)
    )
    if (
        type(selected_adapter) not in {BoundedAdapter}
        or selected_adapter.max_retries.total != 0
    ):
        raise WriteDenied("mutation_retry_policy_unavailable")
    settings = transport_settings(session, request.url or url, stream)
    request.hooks = {"response": []}
    send = cast(
        Callable[..., Response],
        BoundedAdapter.send,
    )
    response = send(
        selected_adapter,
        request,
        stream=stream,
        timeout=timeout,
        verify=settings["verify"],
        cert=settings["cert"],
        proxies=settings["proxies"],
    )
    store_cookies = cast(Callable[[Any, Any, Any], None], extract_cookies_to_jar)
    store_cookies(session.cookies, request, response.raw)
    return response


class _MutationSession:
    """Expose only the core service's POST operation through direct dispatch."""

    def __init__(self, session: Session, adapter: BoundedAdapter | None = None) -> None:
        self.session = session
        self.adapter = adapter

    def post(self, url: str, **kwargs: Any) -> Response:
        """Dispatch the library's unchanged POST payload exactly once."""
        return request_once(self.session, "POST", url, adapter=self.adapter, **kwargs)


@contextmanager
def mutation_session(
    drive: Any, *, adapter: BoundedAdapter | None = None
) -> Generator[Any, None, None]:
    """Keep existing service payload construction, replace only its dispatch path."""
    previous = drive.session
    if isinstance(drive, DriveService):
        yield DriveService(
            drive.service_root,
            drive.document_root,
            cast(Any, _MutationSession(previous, adapter)),
            drive.params,
        )
        return
    drive.session = _MutationSession(previous, adapter)
    try:
        yield drive
    finally:
        drive.session = previous
