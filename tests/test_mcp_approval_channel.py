"""Operator-channel I/O stays inside the existing filesystem sandbox."""

# pylint: disable=protected-access

from collections.abc import Iterator
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from pyicloud.mcp.approval_channel import (
    ApprovalMailbox,
    approval_base_directory,
    approve_interactively,
    stale_deadline,
)
from pyicloud.mcp.authorization import ApprovalLedger, WriteDenied


def approval_preview(ledger: ApprovalLedger) -> Any:
    """Build a synthetic exact standard-tier request without any real mutation."""
    return ledger.preview(
        "reminders.update",
        "Synthetic",
        {"title": "Before"},
        account_binding="A",
        resource_binding="R",
        revision="V",
    )


@pytest.mark.parametrize(
    "deadline,stale",
    [
        (None, False),
        (True, False),
        (float("nan"), False),
        (float("inf"), False),
        (10**400, False),
        (-(10**400), False),
        (0, True),
        (9.0, True),
        (20.0, False),
        (611.0, True),
    ],
)
def test_cleanup_requires_a_finite_established_deadline(
    deadline: Any, stale: bool
) -> None:
    """Unknown or malformed metadata never authorizes deleting a record."""
    assert stale_deadline(deadline, 10) is stale


def test_full_pending_mailbox_still_accepts_receipt(directory: Path) -> None:
    """A valid independent decision has its own bounded slot at preview capacity."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}), capacity=256)
    with ApprovalMailbox(str(directory)) as mailbox:
        previews = [approval_preview(ledger) for _ in range(256)]
        for preview in previews:
            mailbox.publish(preview)
        selected = previews[0]
        mailbox.decide(
            selected.request_id,
            terminal("APPROVE " + selected.request_id),
            io.StringIO(),
        )
        mailbox.accept(selected.request_id, ledger)
        assert (
            ledger.consume(
                selected.request_id,
                account_binding="A",
                resource_binding="R",
                revision="V",
            ).digest
            == selected.digest
        )


def test_expired_mailbox_is_reclaimed_after_restart_without_losing_live_records(
    directory: Path,
) -> None:
    """Validated deadlines free abandoned files while live previews survive reopen."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}), capacity=256, ttl=1)
    with (
        patch("pyicloud.mcp.approval_channel.time.time", return_value=10),
        patch("pyicloud.mcp.approval_channel.time.monotonic", return_value=10),
    ):
        stale = [approval_preview(ledger) for _ in range(255)]
        live_ledger = ApprovalLedger(frozenset({"reminders.update"}), ttl=90)
        live = approval_preview(live_ledger)
        with ApprovalMailbox(str(directory)) as mailbox:
            for preview in [*stale, live]:
                mailbox.publish(preview)
    with (
        patch("pyicloud.mcp.approval_channel.time.time", return_value=12),
        patch("pyicloud.mcp.approval_channel.time.monotonic", return_value=12),
    ):
        fresh = approval_preview(ledger)
        with ApprovalMailbox(str(directory)) as reopened:
            reopened.publish(fresh)
        assert (directory / (live.request_id + ".pending.json")).exists()
        assert not (directory / (stale[0].request_id + ".pending.json")).exists()


@pytest.mark.parametrize("field", ["action", "target", "payload"])
def test_modified_display_cannot_approve_original_payload(
    directory: Path, field: str
) -> None:
    """Retaining a genuine digest cannot authorize content not shown to the operator."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = approval_preview(ledger)
    changes = {
        "action": {"action": "reminders.delete"},
        "target": {"target": "Other"},
        "payload": {"_payload_json": '{"title":"Other"}'},
    }
    altered = replace(preview, **changes[field])
    with ApprovalMailbox(str(directory)) as mailbox:
        mailbox.publish(altered)
        mailbox.decide(
            preview.request_id, terminal("APPROVE " + preview.request_id), io.StringIO()
        )
        with pytest.raises(WriteDenied, match="preview_mismatch"):
            mailbox.accept(preview.request_id, ledger)
    with pytest.raises(WriteDenied, match="operator_approval_required"):
        ledger.consume(
            preview.request_id, account_binding="A", resource_binding="R", revision="V"
        )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO support is unavailable")
def test_fifo_record_is_rejected_without_waiting_for_writer(directory: Path) -> None:
    """Non-blocking opens reach the existing type guard without a FIFO stall."""
    request = "x" * 43
    os.mkfifo(directory / (request + ".pending.json"), 0o600)
    source, display = terminal("APPROVE " + request), io.StringIO()
    with (
        ApprovalMailbox(str(directory)) as mailbox,
        pytest.raises(WriteDenied, match="insecure_approval_record"),
    ):
        mailbox.decide(request, source, display)


def test_explicit_decline_releases_pending_record(directory: Path) -> None:
    """A deliberate terminal decline reclaims storage; a mistyped phrase does not."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = approval_preview(ledger)
    with ApprovalMailbox(str(directory)) as mailbox:
        mailbox.publish(preview)
        source = terminal("DECLINE " + preview.request_id)
        display = io.StringIO()
        with pytest.raises(WriteDenied, match="operator_declined"):
            mailbox.decide(
                preview.request_id,
                source,
                display,
            )
    assert not (directory / (preview.request_id + ".pending.json")).exists()


@pytest.mark.parametrize(
    "deadline", [None, True, float("nan"), float("inf"), "invalid", 10**400, -(10**400)]
)
def test_unusable_deadlines_cannot_be_published_or_decided(
    directory: Path, deadline: Any
) -> None:
    """Missing and nonnumeric persisted deadlines cannot create immortal approvals."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = approval_preview(ledger)
    with ApprovalMailbox(str(directory)) as mailbox:
        malformed = replace(preview, expires_at_wall=deadline)
        with pytest.raises(WriteDenied, match="invalid_approval_record"):
            mailbox.publish(malformed)
        mailbox.publish(preview)
        path = directory / (preview.request_id + ".pending.json")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["expires_at"] = deadline
        path.write_text(json.dumps(record), encoding="utf-8")
        source, display = terminal("APPROVE " + preview.request_id), io.StringIO()
        with pytest.raises(WriteDenied, match="invalid_approval_record"):
            mailbox.decide(preview.request_id, source, display)


def test_independent_mailbox_descriptor_cannot_mutate_while_locked(
    directory: Path,
) -> None:
    """Separate descriptors share one non-blocking coordination boundary."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = approval_preview(ledger)
    with (
        ApprovalMailbox(str(directory)) as first,
        ApprovalMailbox(str(directory)) as second,
    ):
        with (
            first._coordinated(),
            pytest.raises(WriteDenied, match="approval_mailbox_busy"),
        ):
            second.publish(preview)
        second.publish(preview)
        second.close()
        with pytest.raises(WriteDenied, match="operator_channel_closed"):
            second.publish(preview)


@pytest.mark.parametrize("replace_record", [False, True])
def test_operator_input_releases_lock_and_revalidates_record(
    directory: Path, replace_record: bool
) -> None:
    """Waiting releases the lock; a changed preview cannot be accepted."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = approval_preview(ledger)
    path = directory / (preview.request_id + ".pending.json")
    with (
        ApprovalMailbox(str(directory)) as first,
        ApprovalMailbox(str(directory)) as second,
    ):
        first.publish(preview)

        def concurrent_input() -> str:
            """Perform another mailbox action while the operator is thinking."""

            second.publish(approval_preview(ledger))
            record = json.loads(path.read_text(encoding="utf-8"))
            if replace_record:
                path.rename(directory / "original")
            else:
                record["expires_at"] = time.time() - 1
            path.write_text(json.dumps(record), encoding="utf-8")
            path.chmod(0o600)
            return str("APPROVE " + preview.request_id)

        source = MagicMock()
        source.isatty.return_value = True
        source.readline.side_effect = concurrent_input
        display = io.StringIO()
        with pytest.raises(WriteDenied, match="preview_mismatch"):
            first.decide(preview.request_id, source, display)
        assert not (directory / (preview.request_id + ".approved.json")).exists()


def test_failed_sync_removes_only_its_new_record(directory: Path) -> None:
    """An interrupted write cannot permanently consume a preview or receipt slot."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = approval_preview(ledger)
    with ApprovalMailbox(str(directory)) as mailbox:
        with (
            patch(
                "pyicloud.mcp.approval_channel.os.fsync", side_effect=OSError("private")
            ),
            pytest.raises(WriteDenied, match="approval_record_write_failed"),
        ):
            mailbox.publish(preview)
        assert not (directory / (preview.request_id + ".pending.json")).exists()
        mailbox.publish(preview)


def test_failed_write_does_not_remove_replacement_inode(directory: Path) -> None:
    """A private file replaced after creation is never deleted by failure cleanup."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = approval_preview(ledger)
    path = directory / (preview.request_id + ".pending.json")

    def replaced(_fd: int) -> None:
        path.rename(directory / "original")
        path.write_text("replacement", encoding="utf-8")
        path.chmod(0o600)
        raise OSError("private")

    with (
        ApprovalMailbox(str(directory)) as mailbox,
        patch("pyicloud.mcp.approval_channel.os.fsync", side_effect=replaced),
        pytest.raises(WriteDenied, match="approval_record_write_failed"),
    ):
        mailbox.publish(preview)
    assert path.read_text(encoding="utf-8") == "replacement"


@pytest.mark.parametrize("deadline", [None, 10**400, -(10**400)])
def test_aged_malformed_records_are_reclaimed_but_fresh_corruption_is_preserved(
    directory: Path,
    deadline: Any,
) -> None:
    """Only old private single-link regular records can be reclaimed after a crash."""
    age = time.time() - 601
    for index in range(256):
        path = directory / (f"{index:043d}" + ".pending.json")
        content = (
            json.dumps({"request_id": f"{index:043d}", "expires_at": deadline})
            if index == 1
            else "incomplete"
        )
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        if index:
            os.utime(path, (age, age))
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = approval_preview(ledger)
    with ApprovalMailbox(str(directory)) as mailbox:
        mailbox.publish(preview)
    assert (directory / (f"{0:043d}" + ".pending.json")).exists()
    assert not (directory / (f"{1:043d}" + ".pending.json")).exists()


def test_persisted_wall_deadline_does_not_depend_on_rebooted_monotonic_epoch(
    directory: Path,
) -> None:
    """Only in-memory ledger lifetime uses monotonic time; persisted time is UTC."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}), ttl=90)
    with (
        patch("pyicloud.mcp.authorization.time.time", return_value=1000),
        patch("pyicloud.mcp.authorization.time.monotonic", return_value=500),
    ):
        preview = approval_preview(ledger)
        with ApprovalMailbox(str(directory)) as mailbox:
            mailbox.publish(preview)
    with (
        patch("pyicloud.mcp.approval_channel.time.time", return_value=1010),
        patch("pyicloud.mcp.approval_channel.time.monotonic", return_value=1),
        ApprovalMailbox(str(directory)) as reopened,
    ):
        record = reopened._read(preview.request_id, ".pending.json")
        assert record["expires_at"] == 1090
        reopened.decide(
            preview.request_id,
            terminal("APPROVE " + preview.request_id),
            io.StringIO(),
        )


@pytest.fixture(name="directory")
def sandbox_directory() -> Iterator[Path]:
    """Resolve descriptor-relative test paths to an absolute sandbox path."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("Private descriptor-relative approval storage requires POSIX")
    root = Path(tempfile.gettempdir()) / "python-test-results"
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(dir=root))
    original_open, original_unlink, original_stat = os.open, os.unlink, os.stat

    def anchored_open(path: Any, *args: Any, **kwargs: Any) -> int:
        if kwargs.pop("dir_fd", None) is not None:
            path = directory / path
        return original_open(path, *args, **kwargs)

    def anchored_unlink(path: Any, **kwargs: Any) -> None:
        if kwargs.pop("dir_fd", None) is not None:
            path = directory / path
        original_unlink(path, **kwargs)

    def anchored_stat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if kwargs.pop("dir_fd", None) is not None:
            path = directory / path
        return original_stat(path, *args, **kwargs)

    try:
        with (
            patch(
                "pyicloud.mcp.approval_channel.approval_base_directory",
                return_value=str(directory),
            ),
            patch(
                "pyicloud.mcp.approval_channel.os.path.realpath",
                side_effect=os.path.abspath,
            ),
            patch("pyicloud.mcp.approval_channel.os.open", side_effect=anchored_open),
            patch(
                "pyicloud.mcp.approval_channel.entry_stat", side_effect=anchored_stat
            ),
            patch(
                "pyicloud.mcp.approval_channel.os.unlink", side_effect=anchored_unlink
            ),
        ):
            yield directory
    finally:
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()


class SyntheticTerminal(io.StringIO):
    """Only test input, not a claim of actual operator presence."""

    def isatty(self) -> bool:
        """Model an independently interactive operator terminal."""
        return True


def test_unsupported_approval_platform_fails_closed_without_posix_fixture() -> None:
    """The explicit unsupported-platform check must also remain runnable on Windows."""
    with (
        patch("pyicloud.mcp.approval_channel.os.name", "nt"),
        pytest.raises(WriteDenied, match="operator_channel_unsupported"),
    ):
        ApprovalMailbox("unused")


def terminal(value: str) -> io.StringIO:
    """A synthetic terminal response, never actual operator/account input."""
    return SyntheticTerminal(value)


@pytest.mark.parametrize("kind", ["parent", "sibling_prefix", "symlink"])
def test_approval_root_escape_is_rejected_before_open(
    directory: Path, kind: str
) -> None:
    """Canonical traversal and partial-prefix tricks cannot select another mailbox."""
    target = (
        directory / ".." / "outside"
        if kind == "parent"
        else Path(str(directory) + "-outside")
        if kind == "sibling_prefix"
        else directory
    )
    with (
        patch(
            "pyicloud.mcp.approval_channel.os.path.islink",
            return_value=kind == "symlink",
        ),
        patch("pyicloud.mcp.approval_channel.os.open") as opened,
    ):
        with pytest.raises(WriteDenied):
            ApprovalMailbox(str(target))
        opened.assert_not_called()


def test_default_approval_root_comes_from_os_identity(directory: Path) -> None:
    """OS account home, not caller-controlled input, defines containment."""
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel

    with patch("pwd.getpwuid", return_value=SimpleNamespace(pw_dir=str(directory))):
        assert approval_base_directory() == str(
            directory / ".local" / "share" / "pyicloud" / "mcp-approvals"
        )


@pytest.mark.parametrize("bad", ["", "control", "large"])
def test_invalid_approval_directory_is_rejected_before_open(
    directory: Path, bad: str
) -> None:
    """Empty/control/oversized CLI directory data cannot reach filesystem opens."""
    value = (
        "" if not bad else str(directory) + "\x00" if bad == "control" else "x" * 4097
    )
    with patch("pyicloud.mcp.approval_channel.os.open") as opened:
        with pytest.raises(WriteDenied, match="invalid_approval_directory"):
            ApprovalMailbox(value)
        opened.assert_not_called()


def test_operator_decision_is_out_of_band_and_single_use(directory: Path) -> None:
    """Private terminal receipt grants only the exact mutation snapshot."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = ledger.preview(
        "reminders.update",
        "Synthetic",
        {"title": "\x1b[2J"},
        account_binding="ACCOUNT_PRIVATE",
        resource_binding="ROUTE_PRIVATE",
        revision="v1",
    )
    with ApprovalMailbox(str(directory)) as mailbox:
        mailbox.publish(preview)
        with pytest.raises(WriteDenied, match="approval_record_unavailable"):
            mailbox.publish(preview)
        display = io.StringIO()
        mailbox.decide(
            preview.request_id, terminal("APPROVE " + preview.request_id), display
        )
        assert "\x1b" not in display.getvalue()
        assert "ACCOUNT_PRIVATE" not in display.getvalue()
        mailbox.accept(preview.request_id, ledger)
        assert not (directory / (preview.request_id + ".pending.json")).exists()
        assert not (directory / (preview.request_id + ".approved.json")).exists()
        accepted = ledger.consume(
            preview.request_id,
            account_binding="ACCOUNT_PRIVATE",
            resource_binding="ROUTE_PRIVATE",
            revision="v1",
        )
        assert accepted.digest == preview.digest
        with pytest.raises(WriteDenied, match="approval_record_unavailable"):
            mailbox.accept(preview.request_id, ledger)
    mailbox.close()
    with pytest.raises(WriteDenied, match="operator_channel_closed"):
        mailbox.publish(preview)


def test_consumed_receipt_tolerates_already_missing_pending_record(
    directory: Path,
) -> None:
    """Receipt cleanup is exact and remains single-use when pending is absent."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = ledger.preview(
        "reminders.update",
        "Synthetic",
        {},
        account_binding="A",
        resource_binding="R",
        revision="v1",
    )
    with ApprovalMailbox(str(directory)) as mailbox:
        mailbox.publish(preview)
        mailbox.decide(
            preview.request_id, terminal("APPROVE " + preview.request_id), io.StringIO()
        )
        (directory / (preview.request_id + ".pending.json")).unlink()
        mailbox.accept(preview.request_id, ledger)
        assert (
            ledger.consume(
                preview.request_id,
                account_binding="A",
                resource_binding="R",
                revision="v1",
            ).digest
            == preview.digest
        )
        with pytest.raises(WriteDenied, match="approval_record_unavailable"):
            mailbox.accept(preview.request_id, ledger)


def test_non_interactive_and_declined_approval_do_not_create_receipt(
    directory: Path,
) -> None:
    """Piped responses and anything except the exact phrase fail closed."""
    ledger = ApprovalLedger(frozenset({"reminders.update"}))
    preview = ledger.preview(
        "reminders.update",
        "Synthetic",
        {},
        account_binding="A",
        resource_binding="R",
        revision="v1",
    )
    with ApprovalMailbox(str(directory)) as mailbox:
        mailbox.publish(preview)
        source, display = io.StringIO("yes"), io.StringIO()
        with pytest.raises(WriteDenied, match="operator_terminal_required"):
            mailbox.decide(preview.request_id, source, display)
        tty = terminal("yes")
        with pytest.raises(WriteDenied, match="operator_declined"):
            mailbox.decide(preview.request_id, tty, display)
        with pytest.raises(WriteDenied, match="invalid_approval_id"):
            mailbox.decide("../outside", tty, display)


def test_invalid_records_permissions_and_size_are_rejected(directory: Path) -> None:
    """Never accept malformed, oversized or publicly readable approvals."""
    identifier = "x" * 43
    file = directory / (identifier + ".pending.json")
    tty, display = terminal("yes"), io.StringIO()
    with ApprovalMailbox(str(directory)) as mailbox:
        for data, code in [
            (b"broken", "invalid_approval_record"),
            (b"[]", "invalid_approval_record"),
            (b"x" * 262145, "approval_record_too_large"),
        ]:
            file.write_bytes(data)
            file.chmod(0o600)
            with pytest.raises(WriteDenied, match=code):
                mailbox.decide(identifier, tty, display)
        file.write_text(
            json.dumps({"request_id": identifier, "digest": "bad"}), encoding="utf-8"
        )
        file.chmod(0o600)
        with pytest.raises(WriteDenied, match="invalid_approval_record"):
            mailbox.decide(identifier, tty, display)
        file.chmod(0o644)
        with pytest.raises(WriteDenied, match="insecure_approval_record"):
            mailbox.decide(identifier, tty, display)
        with pytest.raises(WriteDenied, match="approval_record_too_large"):
            mailbox._write(identifier, ".huge", {"value": "x" * 262144})
        with (
            patch("pyicloud.mcp.approval_channel.os.listdir", return_value=["x"] * 256),
            pytest.raises(WriteDenied, match="approval_mailbox_full"),
        ):
            mailbox._write(identifier, ".full", {})


def test_directory_requirements_and_standalone_operator_path(directory: Path) -> None:
    """Never create storage silently or use weak platform permissions."""
    directory.chmod(0o777)
    with pytest.raises(WriteDenied, match="insecure_approval_directory"):
        ApprovalMailbox(str(directory))
    directory.chmod(0o700)
    with pytest.raises(WriteDenied, match="approval_directory_unavailable"):
        ApprovalMailbox(str(directory / "missing"))
    with (
        patch("pyicloud.mcp.approval_channel.os.name", "nt"),
        pytest.raises(WriteDenied, match="operator_channel_unsupported"),
    ):
        ApprovalMailbox(str(directory))
    with (
        patch("pyicloud.mcp.approval_channel.sys.stdin", io.StringIO("yes")),
        pytest.raises(WriteDenied, match="operator_terminal_required"),
    ):
        approve_interactively(str(directory), "x" * 43)
