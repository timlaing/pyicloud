"""Direct adapter dispatch without custom session replay or response hooks."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from requests import Request, Response, Session
from requests.adapters import HTTPAdapter

from .authorization import WriteDenied
from .context import BoundedAdapter


def checked_adapter(session: Any, url: str, *, bounded: bool = False) -> HTTPAdapter:
    """Validate the actual longest-prefix adapter before spending resources."""
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


def request_once(session: Session, method: str, url: str, **kwargs: Any) -> Response:
    """Prepare ordinary session cookies, but bypass request/send/hooks/recovery."""
    kwargs.pop("allow_redirects", None)
    stream = kwargs.pop("stream", True)
    timeout = kwargs.pop("timeout", (5.0, 30.0))
    kwargs["headers"] = {**session.headers, **(kwargs.pop("headers", None) or {})}
    kwargs["cookies"] = session.cookies.copy()
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
    return send(
        adapter,
        request,
        stream=stream,
        timeout=timeout,
        verify=session.verify,
        cert=session.cert,
        proxies={},
    )


class _MutationSession:
    """Expose only the core service's POST operation through direct dispatch."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def post(self, url: str, **kwargs: Any) -> Response:
        """Dispatch the library's unchanged POST payload exactly once."""
        return request_once(self.session, "POST", url, **kwargs)


@contextmanager
def mutation_session(drive: Any) -> Iterator[None]:
    """Keep existing service payload construction, replace only its dispatch path."""
    previous = drive.session
    drive.session = _MutationSession(previous)
    try:
        yield
    finally:
        drive.session = previous
