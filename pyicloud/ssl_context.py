"""Context manager to configure SSL verification for requests"""

import contextlib
import warnings
from typing import Any, Callable, Generator, Set

import requests
from urllib3.exceptions import InsecureRequestWarning


@contextlib.contextmanager
def configurable_ssl_verification(
    verify_ssl=True,
    http_proxy: str = "",
    https_proxy: str = "",
) -> Generator[None, Any, None]:
    """Context manager to configure SSL verification for requests"""
    opened_adapters: Set[Any] = set()

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

        # You can also uncomment and use proxies here if needed,
        proxies = {
            "http": http_proxy,
            "https": https_proxy,
        }
        settings["proxies"] = proxies

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
            except Exception:
                pass  # Ignore errors during adapter closing
