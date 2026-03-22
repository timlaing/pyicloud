"""Dedicated integration validator for Reminders delta-sync APIs.

This script validates the currently implemented delta APIs in
`pyicloud.services.reminders.service.RemindersService`:

- sync_cursor()
- iter_changes(since=...)

The validation intentionally checks update and delete in separate cursor windows,
because CloudKit zone changes are a delta-state feed rather than an append-only
event log for individual records.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from getpass import getpass
from time import monotonic, sleep
from typing import Any, Iterable, Optional, Sequence

from pyicloud import PyiCloudService
from pyicloud.services.reminders.models.domain import Reminder, RemindersList


@dataclass
class ValidationTracker:
    checks: int = 0
    failures: list[str] = field(default_factory=list)

    def expect(self, condition: bool, label: str, detail: str = "") -> None:
        self.checks += 1
        if condition:
            print(f"  [PASS] {label}")
            return

        message = label if not detail else f"{label}: {detail}"
        self.failures.append(message)
        print(f"  [FAIL] {message}")


@dataclass
class RunState:
    created: Optional[Reminder] = None
    deleted: bool = False


def banner(title: str) -> None:
    print(f"\n{'=' * 78}")
    print(title)
    print(f"{'=' * 78}")


def parse_args() -> argparse.Namespace:
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--username",
        default=os.getenv("PYICLOUD_USERNAME"),
        help="Apple ID email. Defaults to interactive prompt.",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("PYICLOUD_PASSWORD"),
        help="Apple ID password. Defaults to keyring or interactive prompt.",
    )
    parser.add_argument(
        "--list-name",
        default="pyicloud testing",
        help="Existing reminders list title to use.",
    )
    parser.add_argument(
        "--prefix",
        default=f"pyicloud-reminders-delta-{now}",
        help="Prefix added to the dedicated delta reminder title.",
    )
    parser.add_argument(
        "--consistency-timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for eventual consistency checks.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Polling interval in seconds for eventual consistency checks.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete the generated reminder if the script fails before the delete phase.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print traceback on unexpected errors.",
    )
    return parser.parse_args()


def resolve_credentials(args: argparse.Namespace) -> tuple[str, Optional[str]]:
    username = args.username or input("Apple ID: ").strip()
    if not username:
        raise ValueError("Apple ID username is required.")

    password = args.password
    if password == "":
        password = None

    if password is None and sys.stdin.isatty():
        answer = input("Password not provided. Prompt now? [y/N]: ").strip().lower()
        if answer in {"y", "yes"}:
            password = getpass("Apple ID password: ")

    return username, password


def _prompt_selection(
    prompt: str, options: Sequence[Any], default_index: int = 0
) -> int:
    if not options:
        raise ValueError("Cannot select from an empty option list.")
    selected_index = default_index
    if len(options) > 1:
        raw_index = input(f"{prompt} [{default_index}]: ").strip()
        if raw_index:
            selected_index = int(raw_index)
    if selected_index < 0 or selected_index >= len(options):
        raise RuntimeError("Invalid selection.")
    return selected_index


def _trusted_device_label(device: dict[str, Any]) -> str:
    if device.get("phoneNumber"):
        return "SMS trusted device"
    if device.get("deviceName") or device.get("id"):
        return "Trusted device"
    return "Unknown trusted device"


def authenticate(args: argparse.Namespace) -> PyiCloudService:
    username, password = resolve_credentials(args)
    print("Authenticating with iCloud...")
    api = PyiCloudService(apple_id=username, password=password)

    if api.requires_2fa:
        fido2_devices = list(api.fido2_devices)
        if fido2_devices:
            print("Security key verification required.")
            for index, _device in enumerate(fido2_devices):
                print(f"  {index}: Security key {index}")
            selected_index = _prompt_selection(
                "Select security key",
                fido2_devices,
            )
            selected_device = fido2_devices[selected_index]
            print("Touch the selected security key to continue.")
            try:
                api.confirm_security_key(selected_device)
            except Exception as exc:  # pragma: no cover - live integration path
                raise RuntimeError("Security key verification failed.") from exc
        else:
            code = input("Enter 2FA code: ").strip()
            if not api.validate_2fa_code(code):
                raise RuntimeError("Invalid 2FA code.")
        if not api.is_trusted_session:
            print("Session is not trusted. Requesting trust...")
            api.trust_session()
    elif api.requires_2sa:
        devices = api.trusted_devices
        if not devices:
            raise RuntimeError("2SA required but no trusted devices were returned.")

        print("Trusted devices:")
        for index, device in enumerate(devices):
            print(f"  {index}: {_trusted_device_label(device)}")

        selected_index = _prompt_selection(
            "Select trusted device",
            devices,
        )
        device = devices[selected_index]
        if not api.send_verification_code(device):
            raise RuntimeError("Failed to send 2SA verification code.")

        code = input("Enter 2SA verification code: ").strip()
        if not api.validate_verification_code(device, code):
            raise RuntimeError("Invalid 2SA verification code.")

    return api


def pick_target_list(lists: Iterable[RemindersList], list_name: str) -> RemindersList:
    all_lists = list(lists)
    if not all_lists:
        raise RuntimeError("No reminders lists found in iCloud account.")

    print("Available lists:")
    for lst in all_lists:
        print(f"  - {lst.title} ({lst.id})")

    for lst in all_lists:
        if lst.title == list_name:
            print(f"\nUsing list: {lst.title} ({lst.id})")
            return lst

    raise RuntimeError(
        f"List '{list_name}' not found. "
        f"Please create it first or pass --list-name with an existing list."
    )


def wait_until(
    description: str,
    predicate,
    timeout_seconds: float,
    poll_interval: float,
) -> bool:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if predicate():
            return True
        sleep(poll_interval)
    print(f"  [WARN] Timed out while waiting for: {description}")
    return False


def cleanup_generated(api: PyiCloudService, state: RunState) -> None:
    if state.created is None or state.deleted:
        return

    banner("Cleanup")
    try:
        fresh = api.reminders.get(state.created.id)
    except LookupError:
        print(f"  [SKIP] Not found ({state.created.id})")
        return

    try:
        api.reminders.delete(fresh)
        state.deleted = True
        print(f"  [OK] Deleted {state.created.id}")
    except Exception as exc:  # pragma: no cover - live integration path
        print(f"  [WARN] Cleanup failed for {state.created.id}: {exc}")


def main() -> int:
    args = parse_args()
    tracker = ValidationTracker()
    state = RunState()
    api: Optional[PyiCloudService] = None

    try:
        api = authenticate(args)
        reminders_api = api.reminders

        banner("1) Discover Lists")
        target_list = pick_target_list(reminders_api.lists(), args.list_name)

        banner("2) sync_cursor() + create delta")
        create_cursor = reminders_api.sync_cursor()
        tracker.expect(
            isinstance(create_cursor, str) and bool(create_cursor),
            "sync_cursor() returns a non-empty cursor before create",
            f"cursor={create_cursor!r}",
        )

        created = reminders_api.create(
            list_id=target_list.id,
            title=f"{args.prefix} | create",
            desc="Dedicated reminder for delta-sync validation.",
        )
        state.created = created
        print(f"  [CREATE] delta reminder: {created.id}")

        create_events = []

        def create_visible() -> bool:
            nonlocal create_events
            create_events = list(reminders_api.iter_changes(since=create_cursor))
            return any(
                event.type == "updated"
                and event.reminder_id == created.id
                and event.reminder is not None
                and event.reminder.title == created.title
                for event in create_events
            )

        tracker.expect(
            wait_until(
                "delta create to appear in iter_changes() output",
                create_visible,
                timeout_seconds=args.consistency_timeout,
                poll_interval=args.poll_interval,
            ),
            "iter_changes(since=...) returns an updated event after create",
            f"event_count={len(create_events)}",
        )
        tracker.expect(
            all(
                hasattr(event, "type") and hasattr(event, "reminder_id")
                for event in create_events
            ),
            "iter_changes() returns structured change events after create",
        )

        banner("3) sync_cursor() + update delta")
        update_cursor = reminders_api.sync_cursor()
        tracker.expect(
            isinstance(update_cursor, str) and bool(update_cursor),
            "sync_cursor() returns a non-empty cursor before update",
            f"cursor={update_cursor!r}",
        )

        updated_title = f"{args.prefix} | updated"
        updated_desc = "Updated delta-sync body."
        updated = reminders_api.get(created.id)
        updated.title = updated_title
        updated.desc = updated_desc
        reminders_api.update(updated)

        update_events = []

        def update_visible() -> bool:
            nonlocal update_events
            update_events = list(reminders_api.iter_changes(since=update_cursor))
            return any(
                event.type == "updated"
                and event.reminder_id == created.id
                and event.reminder is not None
                and event.reminder.title == updated_title
                and event.reminder.desc == updated_desc
                for event in update_events
            )

        tracker.expect(
            wait_until(
                "delta update to appear in iter_changes() output",
                update_visible,
                timeout_seconds=args.consistency_timeout,
                poll_interval=args.poll_interval,
            ),
            "iter_changes(since=...) returns an updated event after update",
            f"event_count={len(update_events)}",
        )

        banner("4) sync_cursor() + delete delta")
        delete_cursor = reminders_api.sync_cursor()
        tracker.expect(
            isinstance(delete_cursor, str) and bool(delete_cursor),
            "sync_cursor() returns a non-empty cursor before delete",
            f"cursor={delete_cursor!r}",
        )

        reminders_api.delete(reminders_api.get(created.id))
        state.deleted = True

        delete_events = []

        def delete_visible() -> bool:
            nonlocal delete_events
            delete_events = list(reminders_api.iter_changes(since=delete_cursor))
            return any(
                event.type == "deleted" and event.reminder_id == created.id
                for event in delete_events
            )

        tracker.expect(
            wait_until(
                "delta delete to appear in iter_changes() output",
                delete_visible,
                timeout_seconds=args.consistency_timeout,
                poll_interval=args.poll_interval,
            ),
            "iter_changes(since=...) returns a deleted event after delete",
            f"event_count={len(delete_events)}",
        )

        banner("Coverage Notes")
        print("Validated delta capabilities in current service implementation:")
        print("  - sync_cursor()")
        print("  - iter_changes(since=...) after create")
        print("  - iter_changes(since=...) after update")
        print("  - iter_changes(since=...) after delete")

    except Exception as exc:  # pragma: no cover - live integration path
        banner("Fatal Error")
        print(str(exc))
        if args.debug:
            traceback.print_exc()
        return 1

    finally:
        if args.cleanup and api is not None:
            try:
                cleanup_generated(api, state)
            except Exception as cleanup_exc:  # pragma: no cover
                print(f"[WARN] Cleanup failed: {cleanup_exc}")

    banner("Validation Summary")
    print(f"Checks executed: {tracker.checks}")
    print(f"Failures: {len(tracker.failures)}")

    if tracker.failures:
        print("\nFailure details:")
        for failure in tracker.failures:
            print(f"  - {failure}")
        return 2

    print("All validations passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
