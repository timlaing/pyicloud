"""Private authentication headers and verification-response protocol helpers.

The facade retains session state, endpoints, request ordering and authentication.
These helpers construct a request snapshot or classify an existing response.
"""

from __future__ import annotations

from typing import Any

from requests import Response

from pyicloud.const import AppleAuthError


def build_auth_headers(
    template: dict[str, str],
    idmsa_endpoint: str,
    home_endpoint: str,
    client_id: str,
    session_data: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one request's headers without mutating the template or session."""
    headers: dict[str, Any] = template.copy()
    headers.update({
        "Referer": idmsa_endpoint,
        "X-Apple-OAuth-Redirect-URI": home_endpoint,
        "X-Apple-OAuth-State": client_id,
        "X-Apple-Frame-Id": client_id,
    })
    if session_data.get("scnt"):
        headers["scnt"] = session_data["scnt"]
    if session_data.get("session_id"):
        headers["X-Apple-ID-Session-Id"] = session_data["session_id"]
    if session_data.get("auth_attributes"):
        headers["X-Apple-Auth-Attributes"] = session_data["auth_attributes"]
    if overrides:
        headers.update(overrides)
    return headers


def accepted_security_code_conflict(response: Response | None) -> bool:
    """Return whether a 409 verification response reports the code as valid."""
    if response is None or response.status_code != AppleAuthError.TWO_FACTOR_REQUIRED:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    security_code = body.get("securityCode")
    return isinstance(security_code, dict) and security_code.get("valid") is True


def accepted_security_code_repair(response: Response | None) -> bool:
    """Return whether a 412 verification response opened a repair session."""
    return (
        response is not None
        and response.status_code == AppleAuthError.REPAIR_REQUIRED
        and bool(response.headers.get("X-Apple-Repair-Session-Token"))
    )
