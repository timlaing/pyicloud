"""The pyiCloud library."""

import logging

from pyicloud.base import PyiCloudService

logging.getLogger(__name__).addHandler(logging.NullHandler())


__all__: list[str] = ["PyiCloudService"]
