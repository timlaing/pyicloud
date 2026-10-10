"""Synthetic unreadable documents cannot be overwritten by a reminder update."""

# pylint: disable=protected-access

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.common.cloudkit import CKRecord
from pyicloud.services.reminders._mappers import RemindersRecordMapper
from pyicloud.services.reminders._protocol import CRDTDecodeError, _encode_crdt_document
from pyicloud.services.reminders.service import RemindersService


def record(title: Any, notes: Any) -> Any:
    """Create a synthetic Reminder with independent title and notes documents."""
    return CKRecord.model_validate({
        "recordName": "Reminder/fixture",
        "recordType": "Reminder",
        "fields": {
            "TitleDocument": {"type": "STRING", "value": title},
            "NotesDocument": {"type": "STRING", "value": notes},
        },
    })


@pytest.mark.parametrize("field", ["TitleDocument", "NotesDocument"])
def test_failed_text_read_refuses_even_a_flag_only_update(field: Any) -> None:
    """Failed text read refuses even a flag only update."""
    valid = _encode_crdt_document("Synthetic text")
    invalid = "%%%invalid-document%%%"
    row = record(
        invalid if field == "TitleDocument" else valid,
        invalid if field == "NotesDocument" else valid,
    )
    mapper = RemindersRecordMapper(MagicMock, logging.getLogger(__name__))
    value = mapper.record_to_reminder(row)
    assert value.undecodable_text_fields == (field,)
    assert (value.title if field == "TitleDocument" else value.desc) == ""
    value.flagged = True
    api = RemindersService("https://example.invalid", MagicMock(), {})
    api._raw = MagicMock()
    with pytest.raises(CRDTDecodeError, match="refusing to overwrite"):
        api.update(value)
    api._raw.modify_records.assert_not_called()


def test_valid_text_has_no_failure_marker() -> None:
    """Valid text has no failure marker."""
    mapper = RemindersRecordMapper(MagicMock, logging.getLogger(__name__))
    value = mapper.record_to_reminder(
        record(_encode_crdt_document("Title"), _encode_crdt_document("Notes"))
    )
    assert value.title == "Title" and value.desc == "Notes"
    assert not value.undecodable_text_fields
