"""Internal isolated upload transport; no model URL selectors or runtime tools."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit

from requests import Session
from requests.adapters import HTTPAdapter

from .authorization import WriteDenied
from .context import BoundedAdapter


def checked_upload_url(url: str, hosts: frozenset[str]) -> str:
    """Require exact trusted host selection, HTTPS and no URL credentials."""
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in hosts
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or "\\" in url
        ):
            raise ValueError("Unsafe URL")
    except (ValueError, TypeError):
        raise WriteDenied("unsupported_upload_route") from None
    return url


def bounded_upload_json(response: Any, maximum: int = 262144) -> Any:
    """Bound streamed response bytes and refuse redirects/status ambiguity."""
    try:
        if response.status_code != 200:
            raise WriteDenied("upload_outcome_uncertain")
        data = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not isinstance(chunk, bytes) or len(data) + len(chunk) > maximum:
                raise WriteDenied("upload_outcome_uncertain")
            data.extend(chunk)
        return json.loads(data)
    except Exception:
        raise WriteDenied("upload_outcome_uncertain") from None
    finally:
        response.close()


class UploadTransport:
    """Trusted internal host policy; one dispatch per call and no shared cookies.

    This is not a runtime upload capability. Host sets must come from independently
    reviewed operator/service policy, never a caller or a returned transfer URL.
    """

    def __init__(self, transfer_hosts: frozenset[str]) -> None:
        self.transfer_hosts = transfer_hosts

    def transfer(
        self,
        url: str,
        data: bytes,
        *,
        deadline: float | None = None,
        multipart_name: str | None = None,
    ) -> Any:
        """Send bounded immutable bytes without account auth, proxies or redirects."""
        target = checked_upload_url(url, self.transfer_hosts)
        if (
            not isinstance(data, bytes)
            or len(data) > 4 * 1024 * 1024
            or (not data and multipart_name is None)
        ):
            raise WriteDenied("invalid_upload_bytes")
        if multipart_name is not None and (
            not isinstance(multipart_name, str)
            or not multipart_name
            or multipart_name in {".", ".."}
            or any(
                ord(char) < 32 or char in {"/", "\\", "\x7f"} for char in multipart_name
            )
        ):
            raise WriteDenied("invalid_upload_bytes")
        with Session() as session:
            # A fresh requests session has no account cookies/auth and no retries.
            # Disabling environment inheritance also excludes netrc credentials.
            session.trust_env = False
            adapter = BoundedAdapter(30)
            adapter.deadline = (
                deadline if deadline is not None else time.monotonic() + 30
            )
            adapter.requests_left = 1
            session.mount("https://", adapter)
            try:
                response = session.post(
                    target,
                    **(
                        {"data": data}
                        if multipart_name is None
                        else {"files": {multipart_name: (multipart_name, data)}}
                    ),
                    timeout=(10.0, 60.0),
                    allow_redirects=False,
                    stream=True,
                )
                return bounded_upload_json(response)
            except Exception:
                raise WriteDenied("upload_outcome_uncertain") from None

    @staticmethod
    def register(
        session: Any,
        url: str,
        hosts: frozenset[str],
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        deadline: float | None = None,
    ) -> Any:
        """One raw account-service request; no authentication recovery/replay."""
        target = checked_upload_url(url, hosts)
        options: dict[str, Any] = {"headers": headers} if headers is not None else {}
        try:
            adapter = session.get_adapter(target)
            retries_disabled = (
                isinstance(adapter, HTTPAdapter) and adapter.max_retries.total == 0
            )
        except Exception:
            raise WriteDenied("upload_retry_policy_unavailable") from None
        if not retries_disabled:
            raise WriteDenied("upload_retry_policy_unavailable")
        if deadline is not None:
            if not isinstance(adapter, BoundedAdapter):
                raise WriteDenied("upload_deadline_policy_unavailable")
            if deadline <= time.monotonic():
                raise WriteDenied("upload_deadline_exceeded")
            adapter.deadline = (
                min(adapter.deadline, deadline)
                if adapter.deadline is not None
                else deadline
            )
        try:
            response = session.request_raw(
                "POST",
                target,
                json=payload,
                timeout=(10.0, 60.0),
                allow_redirects=False,
                stream=True,
                **options,
            )
            return bounded_upload_json(response)
        except Exception:
            raise WriteDenied("upload_outcome_uncertain") from None
