"""Context manager to configure SSL verification for requests"""

from collections.abc import Callable, Generator, MutableMapping
import contextlib
import logging
from typing import Any, TypeAlias, cast
import warnings

import requests
from urllib3.exceptions import InsecureRequestWarning

logger: logging.Logger = logging.getLogger(__name__)

_MergeEnvironmentSettings: TypeAlias = Callable[
    [
        "requests.Session",
        str | bytes | None,
        MutableMapping[str, str] | None,
        bool | None,
        bool | str | None,
        str | tuple[str, str] | None,
    ],
    dict[str, Any],
]


@contextlib.contextmanager
def configurable_ssl_verification(
    verify_ssl: bool = True,
    http_proxy: str | None = None,
    https_proxy: str | None = None,
) -> Generator[None, Any, None]:
    """Context manager to configure SSL verification for requests

    Warning: Setting verify_ssl=False disables certificate validation,
    making connections vulnerable to man-in-the-middle attacks.
    Only use in trusted environments for testing purposes.
    """

    # Store the original merge_environment_settings. Cast because requests
    # stubs the return as its private "_Settings" alias for the same type.
    old_merge_environment_settings = cast(
        _MergeEnvironmentSettings,
        requests.Session.merge_environment_settings,
    )

    def merge_environment_settings_with_config(
        self: requests.Session,
        url: str | bytes | None,
        proxies: MutableMapping[str, str] | None,
        stream: bool | None,
        verify: bool | str | None,
        cert: str | tuple[str, str] | None,
    ) -> dict[str, Any]:
        settings = old_merge_environment_settings(
            self, url, proxies, stream, verify, cert
        )

        if not verify_ssl:
            settings["verify"] = False

        # Only set proxies if at least one is non-empty
        override_proxies: dict[str, str] = {}
        if http_proxy:
            override_proxies["http"] = http_proxy
        if https_proxy:
            override_proxies["https"] = https_proxy
        if override_proxies:
            settings["proxies"] = override_proxies
        return settings

    # Temporarily override merge_environment_settings. This is an intentional
    # monkeypatch (same pattern requests itself uses); the target slot is typed
    # with requests' private "_Settings" alias (identical to dict[str, Any]),
    # so the assignment diagnostic is expected here.
    requests.Session.merge_environment_settings = (  # type: ignore[method-assign]
        merge_environment_settings_with_config  # type: ignore[assignment]
    )

    try:
        # Only catch InsecureRequestWarning if we are disabling SSL verification
        if not verify_ssl:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                yield
        else:
            yield
    finally:
        # Restore the original merge_environment_settings
        requests.Session.merge_environment_settings = (  # type: ignore[method-assign]
            old_merge_environment_settings  # type: ignore[assignment]
        )
