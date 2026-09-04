"""Subscription entitlements for Invites writes.

Editing an event is gated behind an iCloud subscription feature. Apple proves
the entitlement with a short-lived token that the write must carry, and without
it every modify comes back ``502 INTERNAL_ERROR`` with nothing to act on -- so
the failure looks like an unsupported operation rather than a missing
credential.

The token comes from a host that is not in the ``webservices`` map Apple
advertises at login, so unlike every other endpoint in this library it cannot
be resolved and is named here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

LOGGER = logging.getLogger(__name__)

#: Not advertised in the webservices map, so unlike every other endpoint
#: here it cannot be resolved from the login response.
GATEWAY_BASE_URL = "https://gatewayws.icloud.com/acsegateway/v4"

#: The capability an event write requires.
CREATE_EVENT_FEATURE = "apps.rsvp.create-event"

#: Re-request a token this long before it expires, so a write near the boundary
#: does not race the clock.
EXPIRY_MARGIN_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class FeatureAccess:
    """Whether an account may use a feature, and the token that proves it."""

    feature_key: str
    can_use: bool
    access_token: str | None = None
    cache_till: datetime | None = None

    def usable_at(self, moment: datetime) -> bool:
        """Return whether this grant is still good at ``moment``."""

        if not self.can_use or not self.access_token:
            return False
        if self.cache_till is None:
            return False
        return (self.cache_till - moment).total_seconds() > EXPIRY_MARGIN_SECONDS


def _parse_cache_till(value: Any) -> datetime | None:
    """Parse Apple's ``cacheTill`` timestamp, tolerating a missing one."""

    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        LOGGER.debug("invites.entitlement.unparsed_cache_till value=%r", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_feature_access(payload: Any, feature: str) -> FeatureAccess:
    """Read one feature's grant out of the gateway's response.

    The response is a list -- one entry per requested feature -- so the wanted
    grant is matched on ``featureKey`` rather than taken positionally.
    """

    entries = payload if isinstance(payload, list) else []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("featureKey") == feature:
            return FeatureAccess(
                feature_key=feature,
                can_use=bool(entry.get("canUse")),
                access_token=entry.get("accessToken"),
                cache_till=_parse_cache_till(entry.get("cacheTill")),
            )
    return FeatureAccess(feature_key=feature, can_use=False)
