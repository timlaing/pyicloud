from __future__ import annotations

import os
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

CloudKitExtraMode = Literal["allow", "ignore", "forbid"]


def resolve_cloudkit_validation_extra(
    explicit: CloudKitExtraMode | None = None,
    *,
    default: CloudKitExtraMode = "allow",
) -> CloudKitExtraMode:
    """
    Resolve the validation mode for CloudKit wire models.

    ``PYICLOUD_CK_EXTRA`` accepts ``allow``, ``ignore``, or ``forbid``.
    Convenience booleans remain supported for local debugging:
      - ``true/1/on/strict`` -> ``forbid``
      - ``false/0/off/lenient`` -> ``allow``

    ``explicit`` takes precedence over the environment.
    """
    if explicit is not None:
        return explicit

    raw = (os.getenv("PYICLOUD_CK_EXTRA") or default).strip().lower()

    if raw in {"allow", "forbid", "ignore"}:
        return cast(CloudKitExtraMode, raw)

    if raw in {"1", "true", "yes", "on", "strict"}:
        return "forbid"
    if raw in {"0", "false", "no", "off", "lenient"}:
        return "allow"

    return default


class CKModel(BaseModel):
    """
    Shared base model for CloudKit wire payloads.

    Wire models stay permissive by default so unexpected Apple fields are
    preserved. Strict reverse-engineering mode is applied at validation call
    sites via ``model_validate(..., extra="forbid")``.
    """

    model_config = ConfigDict(
        extra="allow",
        arbitrary_types_allowed=True,
    )


__all__ = ["CKModel", "CloudKitExtraMode", "resolve_cloudkit_validation_extra"]
