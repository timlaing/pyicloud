"""Shared CloudKit constants for the Reminders service."""

from pyicloud.common.cloudkit import CKZoneID, CKZoneIDReq

_REMINDERS_ZONE = CKZoneID(zoneName="Reminders", zoneType="REGULAR_CUSTOM_ZONE")
_REMINDERS_ZONE_REQ = CKZoneIDReq(
    zoneName="Reminders",
    zoneType="REGULAR_CUSTOM_ZONE",
)
