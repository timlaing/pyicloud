"""Context manager to configure SSL verification for requests"""

import contextlib
import logging
import warnings
from typing import Any, Callable, Generator

import requests
from urllib3.exceptions import InsecureRequestWarning

logger: logging.Logger = logging.getLogger(__name__)


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

    # Store the original merge_environment_settings
    old_merge_environment_settings: Callable = (
        requests.Session.merge_environment_settings
    )

    def merge_environment_settings_with_config(
        self, url, proxies, stream, verify, cert
    ):
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

    # Temporarily override merge_environment_settings
    requests.Session.merge_environment_settings = merge_environment_settings_with_config

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
        requests.Session.merge_environment_settings = old_merge_environment_settings
