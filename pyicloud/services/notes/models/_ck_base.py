from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict


def _env_extra_mode(default: str = "forbid") -> str:
    """
    Determine the extra-mode from environment vars.

    PYICLOUD_NOTES_EXTRA: allow|forbid|ignore
    (fallback) PYICLOUD_EXTRA: allow|forbid|ignore
    Convenience booleans: "true/1/on" -> forbid (strict), "false/0/off" -> allow
    """
    raw = (
        (os.getenv("PYICLOUD_NOTES_EXTRA") or os.getenv("PYICLOUD_EXTRA") or default)
        .strip()
        .lower()
    )

    if raw in {"allow", "forbid", "ignore"}:
        return raw

    # convenience switches people naturally try
    if raw in {"1", "true", "yes", "on", "strict"}:
        return "forbid"
    if raw in {"0", "false", "no", "off", "lenient"}:
        return "allow"

    return default  # fall back to strict during development


_EXTRA = _env_extra_mode()


class CKModel(BaseModel):
    """
    Project-wide base model.

    Default is extra='forbid' (strict) for development; switch at runtime by
    setting an env var before import:
      export PYICLOUD_NOTES_EXTRA=allow   # or forbid/ignore
    """

    model_config = ConfigDict(
        extra=_EXTRA,  # 'forbid' | 'allow' | 'ignore'
        arbitrary_types_allowed=True,  # keep whatever you already relied upon
    )


# Public API of this module
__all__ = ["CKModel", "_env_extra_mode"]
