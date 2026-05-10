"""Backward-compatible Notes CloudKit model exports.

The canonical CloudKit wire models live in :mod:`pyicloud.common.cloudkit.models`.
This module remains as an import compatibility layer for callers that imported
the older Notes-specific model path.
"""

from __future__ import annotations

import pyicloud.common.cloudkit.models as _cloudkit_models
from pyicloud.common.cloudkit.models import *  # noqa: F403

from .constants import NotesDesiredKey as CKDesiredKey
from .constants import NotesRecordType as CKRecordType

__all__ = [name for name in dir(_cloudkit_models) if not name.startswith("_")] + [
    "CKDesiredKey",
    "CKRecordType",
]
