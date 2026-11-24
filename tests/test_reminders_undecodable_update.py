"""Synthetic unreadable documents cannot be overwritten by a reminder update."""

# pylint: disable=protected-access

import base64
import gzip
import logging
from typing import Any
from unittest.mock import MagicMock
import zlib

import pytest

from pyicloud.common.cloudkit import CKRecord
from pyicloud.services.reminders._mappers import RemindersRecordMapper
from pyicloud.services.reminders._protocol import CRDTDecodeError, _encode_crdt_document
from pyicloud.services.reminders.protobuf import reminders_pb2, versioned_document_pb2
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
@pytest.mark.parametrize("invalid", ["%%%invalid-document%%%", "EgH/"])
def test_failed_text_read_refuses_even_a_flag_only_update(
    field: str, invalid: str
) -> None:
    """Failed text read refuses even a flag only update."""
    valid = _encode_crdt_document("Synthetic text")
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
    api._raw.modify.assert_not_called()


def test_valid_text_has_no_failure_marker() -> None:
    """Valid text has no failure marker."""
    mapper = RemindersRecordMapper(MagicMock, logging.getLogger(__name__))
    value = mapper.record_to_reminder(
        record(_encode_crdt_document("Title"), _encode_crdt_document("Notes"))
    )
    assert value.title == "Title"
    assert value.desc == "Notes"
    assert not value.undecodable_text_fields


@pytest.mark.parametrize(
    ("cached_text", "expected"),
    [("Synthetic 🚀 text", "Synthetic 🚀 text"), ("Different length", "")],
)
def test_opaque_attributes_allow_only_validated_display_text(
    cached_text: str, expected: str
) -> None:
    """Recover the cached text only when it matches the substring lengths."""
    document = versioned_document_pb2.Document()  # type: ignore[attr-defined]
    encoded = _encode_crdt_document("Synthetic 🚀 text")
    document.ParseFromString(zlib.decompress(base64.b64decode(encoded)))
    value = reminders_pb2.String()  # type: ignore[attr-defined]
    value.ParseFromString(document.version[0].data)
    value.string = cached_text
    # Field 5 is length-delimited, but its single byte is not an AttributeRun.
    document.version[0].data = value.SerializeToString() + b"\x2a\x01\x08"
    opaque = base64.b64encode(gzip.compress(document.SerializeToString())).decode()
    mapper = RemindersRecordMapper(MagicMock, logging.getLogger(__name__))
    reminder = mapper.record_to_reminder(record(opaque, opaque))

    assert (reminder.title, reminder.desc) == (expected, expected)
    assert reminder.undecodable_text_fields == ("TitleDocument", "NotesDocument")
    api = RemindersService("https://example.invalid", MagicMock(), {})
    api._raw = MagicMock()
    with pytest.raises(CRDTDecodeError, match="refusing to overwrite"):
        api.update(reminder)
    api._raw.modify.assert_not_called()
