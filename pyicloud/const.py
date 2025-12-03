"""Contants for the PyiCloud API."""

from enum import IntEnum

CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_TEXT = "plain/text"
CONTENT_TYPE_TEXT_JSON = "text/json"

HEADER_DATA: dict[str, str] = {
    "X-Apple-ID-Account-Country": "account_country",
    "X-Apple-ID-Session-Id": "session_id",
    "X-Apple-Auth-Attributes": "auth_attributes",
    "X-Apple-Session-Token": "session_token",
    "X-Apple-TwoSV-Trust-Token": "trust_token",
    "X-Apple-TwoSV-Trust-Eligible": "trust_eligible",
    "X-Apple-OAuth-Grant-Code": "grant_code",
    "X-Apple-I-Rscd": "apple_rscd",
    "X-Apple-I-Ercd": "apple_ercd",
    "scnt": "scnt",
}

ACCOUNT_NAME = "accountName"


ERROR_ACCESS_DENIED = "ACCESS_DENIED"
ERROR_ZONE_NOT_FOUND = "ZONE_NOT_FOUND"
ERROR_AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"


class AppleAuthError(IntEnum):
    """Apple auth error codes."""

    SUCCESS = 200
    LOGIN_TOKEN_EXPIRED = 421
    TWO_FACTOR_REQUIRED = 409
    FIND_MY_REAUTH_REQUIRED = 450
    GENERAL_AUTH_ERROR = 500
