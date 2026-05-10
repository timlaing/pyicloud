"""Shared constants for the Notes CloudKit service."""

from pyicloud.common.cloudkit import CKZoneID, CKZoneIDReq

NOTES_ZONE_NAME = "Notes"
NOTES_ZONE = CKZoneID(
    zoneName=NOTES_ZONE_NAME,
    zoneType="REGULAR_CUSTOM_ZONE",
)
NOTES_ZONE_REQ = CKZoneIDReq(zoneName=NOTES_ZONE_NAME)
