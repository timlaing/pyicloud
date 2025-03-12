"""The pyiCloud library."""

import logging

from pyicloud.base import AppleDevice, PyiCloudService

logging.getLogger(__name__).addHandler(logging.NullHandler())


__all__: list[str] = [
    "PyiCloudService",
    "AppleDevice",
]
