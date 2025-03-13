"""Contants for the PyiCloud API."""

CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_TEXT = "plain/text"
CONTENT_TYPE_TEXT_JSON = "text/json"

HEADER_DATA: dict[str, str] = {
    "X-Apple-ID-Account-Country": "account_country",
    "X-Apple-ID-Session-Id": "session_id",
    "X-Apple-Session-Token": "session_token",
    "X-Apple-TwoSV-Trust-Token": "trust_token",
    "X-Apple-I-Rscd": "apple_rscd",
    "X-Apple-I-Ercd": "apple_ercd",
    "scnt": "scnt",
}

ACCOUNT_NAME = "accountName"


ERROR_ACCESS_DENIED = "ACCESS_DENIED"
ERROR_ZONE_NOT_FOUND = "ZONE_NOT_FOUND"
ERROR_AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
