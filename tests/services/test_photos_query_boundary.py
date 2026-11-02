"""Recorded wire shape for Photos query construction before internal extraction."""

from pyicloud.common.cloudkit import CKQueryObject
from pyicloud.services.photos.service import _query_request_payload, _record_name_filter


def test_query_payload_retains_owner_projection_and_cursor() -> None:
    """Shared-owner routing, selected keys and paging survive serialization."""
    zone = {
        "zoneName": "SharedSync-Synthetic",
        "ownerRecordName": "OWNER",
        "zoneType": "REGULAR_CUSTOM_ZONE",
    }
    keys = ["recordName", "recordChangeTag"]
    payload = _query_request_payload(
        query=CKQueryObject(
            recordType="CPLAsset", filterBy=[_record_name_filter("asset")]
        ),
        zone_id=zone,
        desired_keys=keys,
        results_limit=2,
        continuation="next",
    )
    assert payload == {
        "zoneID": zone,
        "desiredKeys": keys,
        "resultsLimit": 2,
        "continuationMarker": "next",
        "query": {
            "recordType": "CPLAsset",
            "filterBy": [
                {
                    "comparator": "EQUALS",
                    "fieldName": "recordName",
                    "fieldValue": {"type": "STRING", "value": "asset"},
                }
            ],
        },
    }
    payload["zoneID"]["zoneName"] = "changed"
    payload["desiredKeys"].append("extra")
    assert zone["zoneName"] == "SharedSync-Synthetic"
    assert keys == ["recordName", "recordChangeTag"]


def test_query_payload_omits_absent_optional_fields() -> None:
    """Unset limits/projections/cursors are omitted rather than serialized as null."""
    assert _query_request_payload(
        query=CKQueryObject(recordType="CPLAsset"), zone_id={"zoneName": "PrimarySync"}
    ) == {
        "zoneID": {"zoneName": "PrimarySync"},
        "query": {"recordType": "CPLAsset"},
    }
