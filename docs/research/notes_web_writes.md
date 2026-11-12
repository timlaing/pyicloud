# Notes Web Write Observations

These observations describe disposable iCloud.com Notes tests. They are not a
public Apple API contract. This file contains no captured record IDs, URLs,
credentials, or note content.

## Checklist and pin state

- Each checklist paragraph carries a 16-byte item ID and an explicit `done`
  value of `0` or `1`. A browser toggle retains the item ID and text, splits
  the affected UTF-16 character span if necessary, and advances its style clock.
- Private pin state is stored in a separate `Note_UserSpecific` record, not in
  the note document. The observed ID is
  `Note_UserSpecific::{owner}/Private::Notes::{owner}::{note_id}`. The owner
  comes from the note's folder reference. Create when the exact metadata record
  is absent; update with its own `recordChangeTag` otherwise. `IsPinned` is an
  integer, and both references use action `NONE`. Preserve the last-viewed date.

## Tags and tables

- A tag is an `InlineAttachment` with UTI
  `com.apple.notes.inlinetextattachment.hashtag`. Its note document run is an
  object marker, not literal hashtag text. The captured record has the note as
  both parent and `Note` reference, plus `CreationDate`, `Deleted`,
  `MinimumSupportedNotesVersion` (7), `UTIEncrypted`, `AltTextEncrypted`, and
  `TokenContentIdentifierEncrypted`. The observed alternate text includes `#`;
  the token identifier was the uppercase ASCII spelling without `#`. Unicode
  normalization has not been verified.
- A table is an `Attachment` with UTI `com.apple.notes.table`. Its payload is a
  zlib-compressed `MergableDataProto` in `MergeableDataEncrypted`. The note
  references the attachment in an object-marker run. Cell edits update the same
  attachment with its own record revision. The observed update sent the
  encrypted UTI and mergeable body, plus explicit null values for
  `MergeableDataAsset`, `EncryptedValues`, and `EncryptedValuesAsset`. The
  browser then advanced the note's `ModificationDate` with an unchanged body
  and the current note revision.
- The existing table reader ignores some fields. The captured table has varint
  version markers at field 1 in the outer and inner envelopes. RegisterLatest
  field 1 has the `TextIdentifier` shape. DictionaryElement field 3 and
  MergeableDataObjectData field 1 contain indexed clock entries with wire
  fields for replica index, clock, and subclock. MergeableDataObjectData field 7
  is a separate `TextVectorTimestamp` with replica UUIDs. A captured cell edit
  advances the table-level clocks; cell Notes did not carry their own vector
  clocks. Do not serialize a new table using only the read renderer's graph.
- Each axis uses an ordered set with object-marker text and two UUID map
  entries per row or column. The ordered-set contents map ordering UUIDs to
  cell-axis UUIDs. `cellColumns` then maps column UUIDs to dictionaries that
  map row UUIDs to cell Notes. Use these links, not object array positions, to
  find a cell in an existing table.
- Adding an inline tag or table spans multiple records. Verify each
  acknowledgment and report partially committed operations without replaying
  an uncertain write.
- The browser writes the inline marker before creating the tag record. The
  note revision returned by the marker acknowledgment can be stale after the
  linked record is created. Read back the note and verify its body before
  returning a current revision to the caller.
- Both table captures parse without unknown protobuf fields. Explicit zero map
  keys and axis indices, and explicitly empty cell text, use presence tracking
  to preserve their wire representation.
