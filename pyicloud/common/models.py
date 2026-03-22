"""Shared strict base models for application-facing service/domain objects."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ServiceModel(BaseModel):
    """Strict base for pyicloud's public service/domain models."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=False,
    )


class FrozenServiceModel(ServiceModel):
    """Strict immutable base for public read-only models."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=False,
        frozen=True,
    )


class MutableServiceModel(ServiceModel):
    """Strict mutable base that validates assignments after construction."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=False,
        validate_assignment=True,
    )


__all__ = ["FrozenServiceModel", "MutableServiceModel", "ServiceModel"]
