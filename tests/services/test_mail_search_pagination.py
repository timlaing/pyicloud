"""Closed-world hardening of the private date-search paging candidate."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from pyicloud.exceptions import PyiCloudConnectionException
from pyicloud.services.mail import MailError, MailFolder, MailMessage
from pyicloud.services.mail._search_pagination import (
    _iter_search_candidates,
    _SearchPage,
    _SearchSource,
    _SearchWindow,
)
from pyicloud.services.mail.models import MailMessageRecord

Case = tuple[MagicMock, MailFolder]


def record(uid: int, milliseconds: int = 9000, **updates: Any) -> MailMessageRecord:
    """Synthetic metadata, with no account data or message content."""
    return MailMessageRecord.model_validate({
        "identifier": f"synthetic-{uid}",
        "uid": uid,
        "mboxRef": {"id": "10"},
        "stateInternalDate": milliseconds / 1000,
        **updates,
    })


def page(
    *records: MailMessageRecord, proved: bool = False, marker: str | None = None
) -> _SearchPage:
    """Only explicit synthetic source evidence may certify a boundary bucket."""
    return _SearchPage(
        tuple(records), boundary_exhausted=proved, continuation_marker=marker
    )


@pytest.fixture(name="case")
def case_fixture() -> Case:
    """A completely offline source with a stable mailbox namespace."""
    folder = MailFolder(identifier="10", name="INBOX", uidValidity=1234)
    source = MagicMock(spec=_SearchSource)
    source.mailbox.return_value = folder
    return source, folder


def scan(case: Case, **options: Any) -> Iterator[MailMessage]:
    """Keep tests on the candidate's full state machine, not isolated helpers."""
    source, folder = case
    return _iter_search_candidates(source, folder, **options)


def test_ties_split_across_pages_are_loaded_before_advancing(case: Case) -> None:
    """A short following page still needs its bucket and an empty terminator."""
    source, folder = case
    source.fetch.side_effect = [
        page(record(10, 10000), record(9)),
        page(record(8), record(9), proved=True),
        page(record(7, 8000)),
        page(record(7, 8000), proved=True),
        page(),
    ]
    iterator = scan(case, page_size=2, max_requests=5)
    source.fetch.assert_not_called()
    source.mailbox.assert_not_called()
    messages = list(iterator)
    assert [m.uid for m in messages] == [10, 9, 8, 7]
    assert all(m.folder == folder for m in messages)
    assert [call.args[1] for call in source.fetch.call_args_list] == [
        _SearchWindow(2),
        _SearchWindow(1000, boundary_ms=9000),
        _SearchWindow(2, before_ms=9000),
        _SearchWindow(1000, boundary_ms=8000),
        _SearchWindow(2, before_ms=8000),
    ]
    assert all(call.args[0] == folder for call in source.fetch.call_args_list)
    assert source.mailbox.call_count == 10


def test_no_prefetch_after_current_page_and_bucket(case: Case) -> None:
    """Partial consumption performs no requests for a later date range."""
    source, _ = case
    source.fetch.side_effect = [
        page(record(9)),
        page(record(9), record(8), proved=True),
    ]
    iterator = scan(case)
    assert next(iterator).uid == 9
    assert next(iterator).uid == 8
    assert source.fetch.call_count == 2


def test_empty_initial_page_is_a_successful_empty_scan(case: Case) -> None:
    """Empty scans require neither a bucket nor an inferred short-page proof."""
    source, _ = case
    source.fetch.return_value = page()
    assert not list(scan(case, max_requests=1))
    source.fetch.assert_called_once()


@pytest.mark.parametrize("count", [1, 999])
def test_under_limit_bucket_requires_explicit_evidence(case: Case, count: int) -> None:
    """Missing markers and short responses must never establish bucket exhaustion."""
    source, _ = case
    source.fetch.side_effect = [
        page(record(1)),
        page(*(record(i) for i in range(1, count + 1))),
    ]
    with pytest.raises(MailError, match="completeness is unverified"):
        next(scan(case))


@pytest.mark.parametrize("evidence", [None, 1, "true", "false"])
def test_truthy_values_are_not_boundary_evidence(case: Case, evidence: Any) -> None:
    """A future adapter must not mistake loosely typed response data for proof."""
    source, _ = case
    source.fetch.side_effect = [page(record(1)), page(record(1), proved=evidence)]
    with pytest.raises(MailError, match="completeness is unverified"):
        next(scan(case))


def test_verified_999_record_tie_is_not_cut_to_the_page_size(case: Case) -> None:
    """A proved synthetic tie bucket may expand an ordinary one-record page."""
    source, _ = case
    records = tuple(record(i) for i in range(1, 1000))
    source.fetch.side_effect = [page(records[0]), page(*records, proved=True), page()]
    result = list(scan(case, page_size=1))
    assert len(result) == 999
    assert {m.uid for m in result} == set(range(1, 1000))


@pytest.mark.parametrize("duplicates", [False, True])
@pytest.mark.parametrize("proved", [False, True])
def test_saturated_bucket_never_yields(
    case: Case, duplicates: bool, proved: bool
) -> None:
    """Even exactly 1,000 records or duplicate-heavy buckets cannot prove exhaustion."""
    source, _ = case
    records = [record(1) if duplicates else record(i) for i in range(1, 1001)]
    source.fetch.side_effect = [page(records[0]), page(*records, proved=proved)]
    with pytest.raises(MailError, match="1000-record ceiling"):
        next(scan(case))


@pytest.mark.parametrize("boundary_request", [False, True])
def test_oversized_response_is_rejected(case: Case, boundary_request: bool) -> None:
    """Limits apply to raw response lengths before identity deduplication."""
    source, _ = case
    source.fetch.side_effect = (
        [page(record(1)), page(*([record(1)] * 1001), proved=True)]
        if boundary_request
        else [page(record(1), record(1))]
    )
    with pytest.raises(MailError, match="exceeded"):
        next(scan(case, page_size=1))


@pytest.mark.parametrize("marker", ["opaque", ""])
@pytest.mark.parametrize("phase", ["page", "bucket", "empty"])
def test_unsupported_continuation_is_not_replayed(
    case: Case, marker: str, phase: str
) -> None:
    """A marker is never silently discarded, including on an empty response."""
    source, _ = case
    source.fetch.side_effect = (
        [page(record(1)), page(record(1), proved=True, marker=marker)]
        if phase == "bucket"
        else [page(*([] if phase == "empty" else [record(1)]), marker=marker)]
    )
    with pytest.raises(MailError, match="unsupported continuation"):
        next(scan(case))


@pytest.mark.parametrize("milliseconds", [8999, 9001])
def test_bucket_scope_is_exact_not_date_equality(case: Case, milliseconds: int) -> None:
    """Records adjacent to the intended timestamp cannot leak through a tie query."""
    source, _ = case
    source.fetch.side_effect = [
        page(record(1)),
        page(record(1), record(2, milliseconds), proved=True),
    ]
    with pytest.raises(MailError, match="outside the boundary bucket"):
        next(scan(case))


@pytest.mark.parametrize(
    "bad_records,reason",
    [
        ([record(1), record(2, 10000)], "descending date"),
        ([record(1), record(2, mboxRef={"id": "wrong"})], "another folder"),
        ([record(1), record(1, identifier="other")], "conflicting UID"),
        ([record(1), record(2, identifier="synthetic-1")], "conflicting message"),
        ([record(1), record(1, flags=32)], "conflicting message"),
    ],
)
def test_invalid_whole_page_never_yields(
    case: Case, bad_records: list[MailMessageRecord], reason: str
) -> None:
    """A later corrupt record invalidates earlier records in the same response."""
    source, _ = case
    source.fetch.return_value = page(*bad_records)
    with pytest.raises(MailError, match=reason):
        next(scan(case))
    source.fetch.assert_called_once()


@pytest.mark.parametrize("phase", ["page", "bucket"])
@pytest.mark.parametrize("date", [None, 1.0001, float("nan"), float("inf"), 1e30])
def test_unsupported_dates_are_never_rounded(case: Case, phase: str, date: Any) -> None:
    """Missing, non-finite, sub-millisecond and unsafe large dates fail closed."""
    source, _ = case
    bad = record(1).model_copy(update={"internal_date": date})
    source.fetch.side_effect = (
        [page(bad)] if phase == "page" else [page(record(1)), page(bad, proved=True)]
    )
    with pytest.raises(MailError, match="date boundary|precision or range"):
        next(scan(case))


def test_millisecond_date_is_preserved_exactly(case: Case) -> None:
    """Fractional seconds with millisecond precision remain valid boundaries."""
    source, _ = case
    item = record(1, 1700000000123)
    source.fetch.side_effect = [page(item), page(item, proved=True), page()]
    assert list(scan(case))[0].internal_date == 1700000000.123
    assert source.fetch.call_args_list[1].args[1].boundary_ms == 1700000000123


def test_zero_and_negative_date_boundaries_are_not_dropped(case: Case) -> None:
    """Zero is a real timestamp, not an absent cursor."""
    source, _ = case
    source.fetch.side_effect = [
        page(record(2, 0)),
        page(record(2, 0), proved=True),
        page(record(1, -1000)),
        page(record(1, -1000), proved=True),
        page(),
    ]
    assert [m.uid for m in scan(case)] == [2, 1]
    assert source.fetch.call_args_list[2].args[1].before_ms == 0
    assert source.fetch.call_args_list[4].args[1].before_ms == -1000


@pytest.mark.parametrize("replacement", [None, "epoch", "wrong-folder"])
@pytest.mark.parametrize("check", [0, 1, 2, 3, 4, 5])
def test_mailbox_changes_abort_at_every_check(
    case: Case, replacement: str | None, check: int
) -> None:
    """Check before and after ordinary, bucket and empty-terminal requests."""
    source, folder = case
    changed = (
        None
        if replacement is None
        else folder.model_copy(
            update={
                "uid_validity": 9999 if replacement == "epoch" else folder.uid_validity,
                "id": "wrong" if replacement == "wrong-folder" else folder.id,
            }
        )
    )
    source.mailbox.side_effect = [*([folder] * check), changed]
    source.fetch.side_effect = [page(record(1)), page(record(1), proved=True), page()]
    with pytest.raises(MailError, match="mailbox identity changed"):
        list(scan(case))


def test_safe_rename_uses_fresh_folder_context(case: Case) -> None:
    """A renamed mailbox in the same UID namespace remains usable."""
    source, folder = case
    renamed = folder.model_copy(update={"name": "Renamed"})
    source.mailbox.return_value = renamed
    source.fetch.side_effect = [page(record(1)), page(record(1), proved=True), page()]
    assert list(scan(case))[0].folder.name == "Renamed"
    assert all(call.args[0] == renamed for call in source.fetch.call_args_list)


@pytest.mark.parametrize("proved", [False, True])
def test_disappearing_boundary_member_fails(case: Case, proved: bool) -> None:
    """A vanished match cannot silently become a completed boundary."""
    source, _ = case
    source.fetch.side_effect = [page(record(1)), page(proved=proved)]
    with pytest.raises(MailError, match="unverified|membership changed"):
        next(scan(case))


@pytest.mark.parametrize(
    "updated,reason",
    [
        (record(1, flags=32), "conflicting message"),
        (record(2, identifier="synthetic-1"), "conflicting message"),
        (record(1, mboxRef={"id": "wrong"}), "another folder"),
        (record(1, identifier="other"), "membership changed"),
    ],
)
def test_page_to_bucket_mutation_is_not_hidden(
    case: Case, updated: MailMessageRecord, reason: str
) -> None:
    """Shared identity, folder and metadata must stay consistent across reads."""
    source, _ = case
    source.fetch.side_effect = [page(record(1)), page(updated, proved=True)]
    with pytest.raises(MailError, match=reason):
        next(scan(case))


@pytest.mark.parametrize("reuse", ["id", "uid"])
def test_changed_timestamp_identity_is_rejected_on_later_page(
    case: Case, reuse: str
) -> None:
    """A prior identity moved below the cursor must not be yielded twice."""
    source, _ = case
    later = record(
        1 if reuse == "uid" else 2,
        8000,
        identifier="synthetic-1" if reuse == "id" else "other",
    )
    source.fetch.side_effect = [
        page(record(1)),
        page(record(1), proved=True),
        page(later),
        page(later, proved=True),
    ]
    iterator = scan(case)
    assert next(iterator).id == "synthetic-1"
    with pytest.raises(MailError, match="reused an identity"):
        next(iterator)


@pytest.mark.parametrize("date", [9000, 10000])
def test_non_advancing_page_does_not_loop(case: Case, date: int) -> None:
    """A repeated or newer date below a strict cursor is a protocol failure."""
    source, _ = case
    source.fetch.side_effect = [
        page(record(1)),
        page(record(1), proved=True),
        page(record(2, date)),
    ]
    iterator = scan(case)
    assert next(iterator).uid == 1
    with pytest.raises(MailError, match="did not honor the date boundary"):
        next(iterator)


@pytest.mark.parametrize("budget", [1, 2])
def test_budget_includes_bucket_and_empty_terminator(case: Case, budget: int) -> None:
    """A spent budget raises before a request and never becomes StopIteration."""
    source, _ = case
    source.fetch.side_effect = [page(record(1)), page(record(1), proved=True)]
    iterator = scan(case, max_requests=budget)
    if budget == 2:
        assert next(iterator).uid == 1
    with pytest.raises(MailError, match="budget exhausted"):
        next(iterator)
    assert source.fetch.call_count == budget


@pytest.mark.parametrize("phase", ["page", "bucket", "later"])
def test_transport_failure_is_not_completion(case: Case, phase: str) -> None:
    """Neither early nor later failures can be translated into successful exhaustion."""
    source, _ = case
    prefix = [] if phase == "page" else [page(record(1))]
    if phase == "later":
        prefix.append(page(record(1), proved=True))
    source.fetch.side_effect = [*prefix, PyiCloudConnectionException("offline")]
    with pytest.raises(PyiCloudConnectionException):
        list(scan(case))


@pytest.mark.parametrize(
    "option,value",
    [
        ("page_size", 0),
        ("page_size", 1001),
        ("page_size", True),
        ("page_size", 1.5),
        ("page_size", "2"),
        ("max_requests", 0),
        ("max_requests", True),
        ("max_requests", 1.5),
        ("max_requests", "2"),
    ],
)
def test_invalid_options_do_not_touch_source(
    case: Case, option: str, value: Any
) -> None:
    """Reject coercions and unsafe limits before reading any metadata."""
    source, _ = case
    with pytest.raises(ValueError):
        list(scan(case, **{option: value}))
    source.fetch.assert_not_called()
    source.mailbox.assert_not_called()


@pytest.mark.parametrize("validity", [None, 0, -1])
def test_unknown_uid_namespace_is_rejected(case: Case, validity: int | None) -> None:
    """Date paging still binds message UIDs to a known mailbox namespace."""
    source, folder = case
    folder = folder.model_copy(update={"uid_validity": validity})
    with pytest.raises(ValueError, match="UID validity"):
        list(_iter_search_candidates(source, folder))
    source.fetch.assert_not_called()
    source.mailbox.assert_not_called()


@dataclass
class _ClosedWorldSource:
    """Only this synthetic in-memory source can honestly attest bucket exhaustion."""

    records: list[MailMessageRecord]
    folder: MailFolder
    windows: list[_SearchWindow] = field(default_factory=list)

    def mailbox(self, folder_id: str) -> MailFolder:
        """The synthetic mailbox has no external mutations."""
        assert folder_id == self.folder.id
        return self.folder

    def fetch(self, folder: MailFolder, window: _SearchWindow) -> _SearchPage:
        """Evaluate a finite known dataset, not a guessed server completeness rule."""
        assert folder == self.folder
        self.windows.append(window)
        selected = sorted(
            self.records, key=lambda r: (r.internal_date or 0, r.uid), reverse=True
        )
        if window.boundary_ms is not None:
            selected = [
                r
                for r in selected
                if round((r.internal_date or 0) * 1000) == window.boundary_ms
            ]
        elif window.before_ms is not None:
            selected = [
                r for r in selected if (r.internal_date or 0) * 1000 < window.before_ms
            ]
        return _SearchPage(
            tuple(selected[: window.limit]),
            boundary_exhausted=len(selected) < window.limit,
        )


def test_closed_world_large_scan_crosses_one_thousand_records(case: Case) -> None:
    """All identities match an independent finite dataset, including date ties."""
    _, folder = case
    records = [record(i, (i // 3) * 1000) for i in range(1, 1004)]
    source = _ClosedWorldSource(records, folder)
    result = list(_iter_search_candidates(source, folder, page_size=50))
    assert len(result) == len(records)
    assert {m.id for m in result} == {r.id for r in records}
    assert len({m.uid for m in result}) == len(records)


def test_new_matches_above_cursor_are_not_claimed_as_a_snapshot(case: Case) -> None:
    """Undetectable concurrent changes remain an explicit limitation of a live scan."""
    source, folder = case
    memory = _ClosedWorldSource([record(2), record(1, 8000)], folder)

    def fetch(current: MailFolder, window: _SearchWindow) -> _SearchPage:
        if window.before_ms == 9000:
            memory.records = [record(3, 10000)]
        return memory.fetch(current, window)

    source.fetch.side_effect = fetch
    assert [m.uid for m in scan(case, page_size=1)] == [2]
    assert {r.uid for r in memory.records} == {3}
