"""Context manager to configure SSL verification for requests"""

import contextlib
import logging
import warnings
from typing import Any, Callable, Generator, Set

import requests
import requests.adapters
from urllib3.exceptions import InsecureRequestWarning

logger: logging.Logger = logging.getLogger(__name__)


@contextlib.contextmanager
def configurable_ssl_verification(
    verify_ssl: bool = True,
    http_proxy: str = None,
    https_proxy: str = None,
) -> Generator[None, Any, None]:
    """Context manager to configure SSL verification for requests"""
    opened_adapters: Set[requests.adapters.BaseAdapter] = set()

    # Store the original merge_environment_settings
    old_merge_environment_settings: Callable = (
        requests.Session.merge_environment_settings
    )

    def merge_environment_settings_with_config(
        self, url, proxies, stream, verify, cert
    ):
        # Add opened adapters to a set so they can be closed later
        opened_adapters.add(self.get_adapter(url))

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

        # Close all opened adapters
        for adapter in opened_adapters:
            try:
                adapter.close()
            except Exception as e:  # pylint: disable=broad-except
                logger.debug("Failed to close adapter: %s", e)
