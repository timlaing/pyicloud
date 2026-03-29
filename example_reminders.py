"""Comprehensive integration validator for the pyicloud Reminders service.

This script exercises the snapshot/read/write surface of
`pyicloud.services.reminders.service.RemindersService` and validates
round-trip behavior against iCloud.

Validated API surface:
- lists()
- reminders(list_id=...) and reminders()
- get(reminder_id)
- create(...) across supported field combinations
- update(reminder)
- add_location_trigger(...)
- create_hashtag(...) / delete_hashtag(...)
- create_url_attachment(...) / update_attachment(...) / delete_attachment(...)
- create_recurrence_rule(...) / update_recurrence_rule(...) / delete_recurrence_rule(...)
- alarms_for(reminder)
- tags_for(reminder)
- attachments_for(reminder)
- recurrence_rules_for(reminder)
- list_reminders(list_id, include_completed=...)
- delete(reminder)

Notes:
- The script writes real reminders into your iCloud account.
- Use `--cleanup` to soft-delete generated reminders at the end.
- Delta APIs are validated separately by `example_reminders_delta.py`.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from getpass import getpass
from time import monotonic, sleep
from typing import Any, Callable, Dict, Iterable, Optional, Sequence

from pyicloud import PyiCloudService
from pyicloud.services.reminders.models.domain import (
    Proximity,
    RecurrenceFrequency,
    Reminder,
    RemindersList,
)

PRIORITY_NONE = 0
PRIORITY_HIGH = 1
PRIORITY_MEDIUM = 5
PRIORITY_LOW = 9


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
    created: Dict[str, Reminder] = field(default_factory=dict)
    deleted_ids: set[str] = field(default_factory=set)


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
        default=f"pyicloud-reminders-validation-{now}",
        help="Prefix added to created reminder titles.",
    )
    parser.add_argument(
        "--results-limit",
        type=int,
        default=500,
        help="Result limit for compound list_reminders query.",
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
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Short delay between write-heavy operations.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Soft-delete generated reminders at the end.",
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

    # Keep None so keyring/session-based auth can work.
    password = args.password
    if password == "":
        password = None

    # If no password was provided via args/env, optionally prompt only when stdin
    # is interactive and the caller chooses to provide one.
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


def _raw_token(value: str) -> str:
    if "/" not in value:
        return value
    return value.split("/", 1)[1]


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
        for index, _device in enumerate(devices):
            print(f"  {index}: Trusted device")

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


def approximately_same_time(
    left: Optional[datetime], right: Optional[datetime], tolerance_seconds: int = 1
) -> bool:
    if left is None or right is None:
        return left is right

    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)

    return abs(left.timestamp() - right.timestamp()) <= tolerance_seconds


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
    banner("Cleanup")
    for case_name, reminder in state.created.items():
        if reminder.id in state.deleted_ids:
            print(f"  [SKIP] {case_name}: already deleted ({reminder.id})")
            continue

        try:
            fresh = api.reminders.get(reminder.id)
        except LookupError:
            print(f"  [SKIP] {case_name}: not found ({reminder.id})")
            continue

        try:
            api.reminders.delete(fresh)
            state.deleted_ids.add(reminder.id)
            print(f"  [OK] Deleted {case_name}: {reminder.id}")
        except Exception as exc:  # pragma: no cover - live integration path
            print(f"  [WARN] Failed deleting {case_name} ({reminder.id}): {exc}")


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

        banner("2) Baseline Reads")
        baseline_list_items = list(reminders_api.reminders(list_id=target_list.id))
        baseline_global_items = list(reminders_api.reminders())
        baseline_compound = reminders_api.list_reminders(
            target_list.id,
            include_completed=True,
            results_limit=args.results_limit,
        )
        print(f"  Baseline list-scoped reminders: {len(baseline_list_items)}")
        print(f"  Baseline global reminders: {len(baseline_global_items)}")
        print(
            "  Baseline compound counts: "
            f"reminders={len(baseline_compound.reminders)}, "
            f"alarms={len(baseline_compound.alarms)}, "
            f"triggers={len(baseline_compound.triggers)}, "
            f"attachments={len(baseline_compound.attachments)}, "
            f"hashtags={len(baseline_compound.hashtags)}, "
            f"recurrence_rules={len(baseline_compound.recurrence_rules)}"
        )

        def create_case(
            case_name: str,
            suffix: str,
            *,
            desc: str,
            completed: bool = False,
            due_date: Optional[datetime] = None,
            priority: int = PRIORITY_NONE,
            flagged: bool = False,
            all_day: bool = False,
            time_zone_name: Optional[str] = None,
            parent_reminder_id: Optional[str] = None,
        ) -> Reminder:
            title = f"{args.prefix} | {suffix}"
            reminder = reminders_api.create(
                target_list.id,
                title=title,
                desc=desc,
                completed=completed,
                due_date=due_date,
                priority=priority,
                flagged=flagged,
                all_day=all_day,
                time_zone=time_zone_name,
                parent_reminder_id=parent_reminder_id,
            )
            state.created[case_name] = reminder
            print(f"  [CREATE] {case_name}: {reminder.id}")
            if args.sleep_seconds > 0:
                sleep(args.sleep_seconds)
            return reminder

        def assert_round_trip(
            case_name: str,
            reminder_id: str,
            *,
            expected_title: Optional[str] = None,
            expected_desc: Optional[str] = None,
            expected_completed: Optional[bool] = None,
            expected_due_date: Optional[datetime] = None,
            expected_priority: Optional[int] = None,
            expected_flagged: Optional[bool] = None,
            expected_all_day: Optional[bool] = None,
            expected_time_zone: Optional[str] = None,
            expected_parent_reminder_id: Optional[str] = None,
        ) -> Reminder:
            matched: dict[str, Optional[Reminder]] = {"reminder": None}

            def _matches_expectations(fresh: Reminder) -> bool:
                if expected_title is not None and fresh.title != expected_title:
                    return False
                if expected_desc is not None and fresh.desc != expected_desc:
                    return False
                if (
                    expected_completed is not None
                    and fresh.completed != expected_completed
                ):
                    return False
                if expected_due_date is not None and not approximately_same_time(
                    fresh.due_date,
                    expected_due_date,
                ):
                    return False
                if (
                    expected_priority is not None
                    and fresh.priority != expected_priority
                ):
                    return False
                if expected_flagged is not None and fresh.flagged != expected_flagged:
                    return False
                if expected_all_day is not None and fresh.all_day != expected_all_day:
                    return False
                if (
                    expected_time_zone is not None
                    and fresh.time_zone != expected_time_zone
                ):
                    return False
                if (
                    expected_parent_reminder_id is not None
                    and fresh.parent_reminder_id != expected_parent_reminder_id
                ):
                    return False
                return True

            def _poll_round_trip() -> bool:
                try:
                    fresh = reminders_api.get(reminder_id)
                except LookupError:
                    return False
                if not _matches_expectations(fresh):
                    return False
                matched["reminder"] = fresh
                return True

            wait_until(
                f"{case_name} round-trip consistency",
                _poll_round_trip,
                args.consistency_timeout,
                args.poll_interval,
            )

            fresh = matched["reminder"]
            if fresh is None:
                fresh = reminders_api.get(reminder_id)

            if expected_title is not None:
                tracker.expect(
                    fresh.title == expected_title,
                    f"{case_name}: title round-trip",
                    f"expected={expected_title!r}, got={fresh.title!r}",
                )

            if expected_desc is not None:
                tracker.expect(
                    fresh.desc == expected_desc,
                    f"{case_name}: desc round-trip",
                    f"expected={expected_desc!r}, got={fresh.desc!r}",
                )

            if expected_completed is not None:
                tracker.expect(
                    fresh.completed == expected_completed,
                    f"{case_name}: completed round-trip",
                    f"expected={expected_completed}, got={fresh.completed}",
                )

            if expected_due_date is not None:
                tracker.expect(
                    approximately_same_time(fresh.due_date, expected_due_date),
                    f"{case_name}: due_date round-trip",
                    f"expected={expected_due_date}, got={fresh.due_date}",
                )

            if expected_priority is not None:
                tracker.expect(
                    fresh.priority == expected_priority,
                    f"{case_name}: priority round-trip",
                    f"expected={expected_priority}, got={fresh.priority}",
                )

            if expected_flagged is not None:
                tracker.expect(
                    fresh.flagged == expected_flagged,
                    f"{case_name}: flagged round-trip",
                    f"expected={expected_flagged}, got={fresh.flagged}",
                )

            if expected_all_day is not None:
                tracker.expect(
                    fresh.all_day == expected_all_day,
                    f"{case_name}: all_day round-trip",
                    f"expected={expected_all_day}, got={fresh.all_day}",
                )

            if expected_time_zone is not None:
                tracker.expect(
                    fresh.time_zone == expected_time_zone,
                    f"{case_name}: time_zone round-trip",
                    f"expected={expected_time_zone!r}, got={fresh.time_zone!r}",
                )

            if expected_parent_reminder_id is not None:
                tracker.expect(
                    fresh.parent_reminder_id == expected_parent_reminder_id,
                    f"{case_name}: parent reminder round-trip",
                    (
                        f"expected={expected_parent_reminder_id!r}, "
                        f"got={fresh.parent_reminder_id!r}"
                    ),
                )

            return fresh

        def wait_for_reminder(
            description: str,
            reminder_id: str,
            predicate: Callable[[Reminder], bool],
            *,
            allow_missing: bool = False,
        ) -> tuple[Optional[Reminder], bool]:
            matched: dict[str, Optional[Reminder] | bool] = {
                "reminder": None,
                "missing": False,
            }

            def _poll() -> bool:
                try:
                    fresh = reminders_api.get(reminder_id)
                except LookupError:
                    matched["reminder"] = None
                    matched["missing"] = True
                    return allow_missing

                matched["missing"] = False
                if not predicate(fresh):
                    return False
                matched["reminder"] = fresh
                return True

            wait_until(
                description,
                _poll,
                args.consistency_timeout,
                args.poll_interval,
            )
            return matched["reminder"], bool(matched["missing"])

        def wait_for_linked_id(
            description: str,
            reminder_id: str,
            attr_name: str,
            linked_id: str,
            *,
            present: bool,
        ) -> Reminder:
            expected_raw_id = _raw_token(linked_id)
            fresh, _ = wait_for_reminder(
                description,
                reminder_id,
                lambda reminder: (
                    any(
                        _raw_token(item) == expected_raw_id
                        for item in getattr(reminder, attr_name)
                    )
                    if present
                    else all(
                        _raw_token(item) != expected_raw_id
                        for item in getattr(reminder, attr_name)
                    )
                ),
            )
            if fresh is None:
                fresh = reminders_api.get(reminder_id)
            return fresh

        def wait_for_relationship_rows(
            description: str,
            reminder_id: str,
            fetch_rows: Callable[[Reminder], list[Any]],
            predicate: Callable[[list[Any]], bool],
        ) -> tuple[Reminder, list[Any]]:
            matched: dict[str, Any] = {"reminder": None, "rows": None}

            def _poll() -> bool:
                try:
                    fresh = reminders_api.get(reminder_id)
                except LookupError:
                    return False
                rows = fetch_rows(fresh)
                if not predicate(rows):
                    return False
                matched["reminder"] = fresh
                matched["rows"] = rows
                return True

            wait_until(
                description,
                _poll,
                args.consistency_timeout,
                args.poll_interval,
            )

            fresh = matched["reminder"]
            if fresh is None:
                fresh = reminders_api.get(reminder_id)
            rows = matched["rows"]
            if rows is None:
                rows = fetch_rows(fresh)
            return fresh, rows

        banner("3) Create Matrix (All Supported create() Configurations)")
        due_aware = (datetime.now(tz=timezone.utc) + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        due_naive = (datetime.utcnow() + timedelta(days=2)).replace(
            hour=11, minute=15, second=0, microsecond=0
        )
        due_naive_expected = due_naive.replace(tzinfo=timezone.utc)
        all_day_due = (datetime.now(tz=timezone.utc) + timedelta(days=3)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        paris_due = (datetime.now(tz=timezone.utc) + timedelta(days=4)).replace(
            hour=14, minute=30, second=0, microsecond=0
        )

        basic = create_case(
            "basic",
            "basic",
            desc="Basic reminder with title and notes.",
        )
        assert_round_trip(
            "basic",
            basic.id,
            expected_title=f"{args.prefix} | basic",
            expected_desc="Basic reminder with title and notes.",
            expected_priority=PRIORITY_NONE,
            expected_flagged=False,
            expected_all_day=False,
        )

        child_case = create_case(
            "child_reminder",
            "child reminder",
            desc="Child reminder linked to the basic reminder.",
            parent_reminder_id=basic.id,
        )
        assert_round_trip(
            "child_reminder",
            child_case.id,
            expected_parent_reminder_id=basic.id,
        )

        completed = create_case(
            "completed_on_create",
            "completed on create",
            desc="Created with completed=True.",
            completed=True,
        )
        assert_round_trip(
            "completed_on_create",
            completed.id,
            expected_completed=True,
        )

        due_aware_case = create_case(
            "due_aware",
            "due aware",
            desc="Timezone-aware due date.",
            due_date=due_aware,
        )
        assert_round_trip(
            "due_aware",
            due_aware_case.id,
            expected_due_date=due_aware,
        )

        due_naive_case = create_case(
            "due_naive",
            "due naive",
            desc="Naive due date should be interpreted as UTC by service code.",
            due_date=due_naive,
        )
        assert_round_trip(
            "due_naive",
            due_naive_case.id,
            expected_due_date=due_naive_expected,
        )

        all_day_case = create_case(
            "all_day",
            "all day",
            desc="All-day reminder.",
            due_date=all_day_due,
            all_day=True,
        )
        assert_round_trip(
            "all_day",
            all_day_case.id,
            expected_due_date=all_day_due,
            expected_all_day=True,
        )

        high_case = create_case(
            "priority_high_flagged",
            "priority high flagged",
            desc="High priority and flagged.",
            priority=PRIORITY_HIGH,
            flagged=True,
        )
        assert_round_trip(
            "priority_high_flagged",
            high_case.id,
            expected_priority=PRIORITY_HIGH,
            expected_flagged=True,
        )

        medium_case = create_case(
            "priority_medium",
            "priority medium",
            desc="Medium priority.",
            priority=PRIORITY_MEDIUM,
        )
        assert_round_trip(
            "priority_medium",
            medium_case.id,
            expected_priority=PRIORITY_MEDIUM,
        )

        low_case = create_case(
            "priority_low",
            "priority low",
            desc="Low priority.",
            priority=PRIORITY_LOW,
        )
        assert_round_trip(
            "priority_low",
            low_case.id,
            expected_priority=PRIORITY_LOW,
        )

        tz_due_case = create_case(
            "timezone_due",
            "timezone due",
            desc="Reminder with explicit time_zone and due date.",
            due_date=paris_due,
            time_zone_name="Europe/Paris",
        )
        assert_round_trip(
            "timezone_due",
            tz_due_case.id,
            expected_due_date=paris_due,
            expected_time_zone="Europe/Paris",
        )

        tz_only_case = create_case(
            "timezone_only",
            "timezone only",
            desc="Reminder with time_zone only.",
            time_zone_name="UTC",
        )
        assert_round_trip(
            "timezone_only",
            tz_only_case.id,
            expected_time_zone="UTC",
        )

        full_case = create_case(
            "full_combo",
            "full combo",
            desc="Create with due date, priority, flagged, and time zone.",
            due_date=due_aware + timedelta(days=7),
            priority=PRIORITY_HIGH,
            flagged=True,
            all_day=False,
            time_zone_name="UTC",
        )
        assert_round_trip(
            "full_combo",
            full_case.id,
            expected_priority=PRIORITY_HIGH,
            expected_flagged=True,
            expected_all_day=False,
            expected_time_zone="UTC",
        )

        location_arrive = create_case(
            "location_arriving",
            "location arriving",
            desc="Location trigger with arriving proximity.",
        )
        location_leave = create_case(
            "location_leaving",
            "location leaving",
            desc="Location trigger with leaving proximity.",
        )
        linked_case = create_case(
            "linked_records",
            "linked records",
            desc="Hashtag + attachment + recurrence write validation case.",
        )

        delete_candidate = create_case(
            "delete_candidate",
            "delete candidate",
            desc="Will be deleted to validate delete path.",
        )

        banner("4) update() Round-Trip")
        updated_basic = reminders_api.get(basic.id)
        updated_basic.title = f"{args.prefix} | basic updated"
        updated_basic.desc = "Updated via update() validation path."
        updated_basic.completed = True
        reminders_api.update(updated_basic)

        post_update = assert_round_trip(
            "update_basic_step1",
            basic.id,
            expected_title=f"{args.prefix} | basic updated",
            expected_desc="Updated via update() validation path.",
            expected_completed=True,
        )

        post_update.completed = False
        reminders_api.update(post_update)
        assert_round_trip(
            "update_basic_step2",
            basic.id,
            expected_completed=False,
        )

        banner("5) add_location_trigger() + alarms_for()")
        pre_alarm_basic = reminders_api.alarms_for(reminders_api.get(medium_case.id))
        tracker.expect(
            pre_alarm_basic == [],
            "alarms_for() returns empty list for reminders without alarms",
            f"got={pre_alarm_basic}",
        )

        arrive_alarm, arrive_trigger = reminders_api.add_location_trigger(
            reminders_api.get(location_arrive.id),
            title="Eiffel Tower",
            address="Champ de Mars, 5 Av. Anatole France, 75007 Paris, France",
            latitude=48.8584,
            longitude=2.2945,
            radius=150.0,
            proximity=Proximity.ARRIVING,
        )
        tracker.expect(
            arrive_trigger.proximity == Proximity.ARRIVING,
            "add_location_trigger() returns ARRIVING trigger",
        )

        leave_alarm, leave_trigger = reminders_api.add_location_trigger(
            reminders_api.get(location_leave.id),
            title="Gare de Luxembourg",
            address="Place de la Gare, 1616 Luxembourg",
            latitude=49.6004,
            longitude=6.1345,
            radius=200.0,
            proximity=Proximity.LEAVING,
        )
        tracker.expect(
            leave_trigger.proximity == Proximity.LEAVING,
            "add_location_trigger() returns LEAVING trigger",
        )

        location_arrive_fresh, arrive_alarm_rows = wait_for_relationship_rows(
            "ARRIVING location trigger to round-trip",
            location_arrive.id,
            reminders_api.alarms_for,
            lambda rows: any(
                row.alarm.id == arrive_alarm.id
                and row.trigger is not None
                and row.trigger.id == arrive_trigger.id
                for row in rows
            ),
        )
        location_leave_fresh, leave_alarm_rows = wait_for_relationship_rows(
            "LEAVING location trigger to round-trip",
            location_leave.id,
            reminders_api.alarms_for,
            lambda rows: any(
                row.alarm.id == leave_alarm.id
                and row.trigger is not None
                and row.trigger.id == leave_trigger.id
                for row in rows
            ),
        )

        tracker.expect(
            len(location_arrive_fresh.alarm_ids) >= 1,
            "ARRIVING reminder has alarm_ids after trigger creation",
            f"alarm_ids={location_arrive_fresh.alarm_ids}",
        )
        tracker.expect(
            len(location_leave_fresh.alarm_ids) >= 1,
            "LEAVING reminder has alarm_ids after trigger creation",
            f"alarm_ids={location_leave_fresh.alarm_ids}",
        )

        arrive_match = next(
            (row for row in arrive_alarm_rows if row.alarm.id == arrive_alarm.id),
            None,
        )
        leave_match = next(
            (row for row in leave_alarm_rows if row.alarm.id == leave_alarm.id),
            None,
        )

        tracker.expect(
            arrive_match is not None,
            "alarms_for() returns created ARRIVING alarm",
            f"alarm_id={arrive_alarm.id}",
        )
        tracker.expect(
            leave_match is not None,
            "alarms_for() returns created LEAVING alarm",
            f"alarm_id={leave_alarm.id}",
        )

        if arrive_match is not None and arrive_match.trigger is not None:
            tracker.expect(
                arrive_match.trigger.id == arrive_trigger.id,
                "alarms_for() returns matching ARRIVING trigger",
            )

        if leave_match is not None and leave_match.trigger is not None:
            tracker.expect(
                leave_match.trigger.id == leave_trigger.id,
                "alarms_for() returns matching LEAVING trigger",
            )

        banner("6) tags_for() / attachments_for() / recurrence_rules_for()")
        arrive_tags = reminders_api.tags_for(location_arrive_fresh)
        arrive_attachments = reminders_api.attachments_for(location_arrive_fresh)
        arrive_recurrence_rules = reminders_api.recurrence_rules_for(
            location_arrive_fresh
        )
        tracker.expect(
            isinstance(arrive_tags, list),
            "tags_for() returns a list",
        )
        tracker.expect(
            isinstance(arrive_attachments, list),
            "attachments_for() returns a list",
        )
        tracker.expect(
            isinstance(arrive_recurrence_rules, list),
            "recurrence_rules_for() returns a list",
        )
        tracker.expect(
            len(arrive_tags) == 0,
            "tags_for() default is empty on new reminder",
            f"got={arrive_tags}",
        )
        tracker.expect(
            len(arrive_attachments) == 0,
            "attachments_for() default is empty on new reminder",
            f"got={arrive_attachments}",
        )
        tracker.expect(
            len(arrive_recurrence_rules) == 0,
            "recurrence_rules_for() default is empty on new reminder",
            f"got={arrive_recurrence_rules}",
        )

        banner("7) Extended Write APIs (hashtags/attachments/recurrence)")
        linked_fresh = reminders_api.get(linked_case.id)

        hashtag_created = reminders_api.create_hashtag(linked_fresh, "pyicloud")
        linked_fresh = wait_for_linked_id(
            "created hashtag ID to appear on linked reminder",
            linked_case.id,
            "hashtag_ids",
            hashtag_created.id,
            present=True,
        )
        tracker.expect(
            any(
                hid == hashtag_created.id.split("/", 1)[1]
                for hid in linked_fresh.hashtag_ids
            ),
            "create_hashtag() links hashtag ID on reminder",
            f"hashtag_ids={linked_fresh.hashtag_ids}",
        )
        linked_fresh, fetched_tags = wait_for_relationship_rows(
            "created hashtag to appear in tags_for()",
            linked_case.id,
            reminders_api.tags_for,
            lambda rows: any(tag.id == hashtag_created.id for tag in rows),
        )
        fetched_tag = next(
            (tag for tag in fetched_tags if tag.id == hashtag_created.id), None
        )
        tracker.expect(
            fetched_tag is not None,
            "tags_for() returns created hashtag",
            f"hashtag_id={hashtag_created.id}",
        )
        if fetched_tag is not None:
            print(
                "  [INFO] Skipping update_hashtag(): "
                "Hashtag.Name is read-only in the iCloud Reminders web app"
            )

        attachment_created = reminders_api.create_url_attachment(
            linked_fresh,
            url="https://example.com/reminders",
            uti="public.url",
        )
        linked_fresh = wait_for_linked_id(
            "created attachment ID to appear on linked reminder",
            linked_case.id,
            "attachment_ids",
            attachment_created.id,
            present=True,
        )
        tracker.expect(
            any(
                aid == attachment_created.id.split("/", 1)[1]
                for aid in linked_fresh.attachment_ids
            ),
            "create_url_attachment() links attachment ID on reminder",
            f"attachment_ids={linked_fresh.attachment_ids}",
        )
        linked_fresh, fetched_attachments = wait_for_relationship_rows(
            "created attachment to appear in attachments_for()",
            linked_case.id,
            reminders_api.attachments_for,
            lambda rows: any(att.id == attachment_created.id for att in rows),
        )
        fetched_attachment = next(
            (att for att in fetched_attachments if att.id == attachment_created.id),
            None,
        )
        tracker.expect(
            fetched_attachment is not None,
            "attachments_for() returns created URL attachment",
            f"attachment_id={attachment_created.id}",
        )
        if fetched_attachment is not None:
            reminders_api.update_attachment(
                fetched_attachment,
                url="https://example.org/reminders",
            )
            linked_fresh, updated_attachments = wait_for_relationship_rows(
                "updated URL attachment to round-trip",
                linked_case.id,
                reminders_api.attachments_for,
                lambda rows: any(
                    att.id == fetched_attachment.id
                    and getattr(att, "url", None) == "https://example.org/reminders"
                    for att in rows
                ),
            )
            tracker.expect(
                any(
                    att.id == fetched_attachment.id
                    and getattr(att, "url", None) == "https://example.org/reminders"
                    for att in updated_attachments
                ),
                "update_attachment() updates URL attachment",
            )
            attachment_created = next(
                (att for att in updated_attachments if att.id == fetched_attachment.id),
                fetched_attachment,
            )

        recurrence_created = reminders_api.create_recurrence_rule(
            linked_fresh,
            frequency=RecurrenceFrequency.WEEKLY,
            interval=2,
            occurrence_count=0,
            first_day_of_week=1,
        )
        linked_fresh = wait_for_linked_id(
            "created recurrence rule ID to appear on linked reminder",
            linked_case.id,
            "recurrence_rule_ids",
            recurrence_created.id,
            present=True,
        )
        tracker.expect(
            any(
                rid == recurrence_created.id.split("/", 1)[1]
                for rid in linked_fresh.recurrence_rule_ids
            ),
            "create_recurrence_rule() links recurrence ID on reminder",
            f"recurrence_rule_ids={linked_fresh.recurrence_rule_ids}",
        )
        linked_fresh, fetched_rules = wait_for_relationship_rows(
            "created recurrence rule to appear in recurrence_rules_for()",
            linked_case.id,
            reminders_api.recurrence_rules_for,
            lambda rows: any(rule.id == recurrence_created.id for rule in rows),
        )
        fetched_rule = next(
            (rule for rule in fetched_rules if rule.id == recurrence_created.id),
            None,
        )
        tracker.expect(
            fetched_rule is not None,
            "recurrence_rules_for() returns created recurrence rule",
            f"rule_id={recurrence_created.id}",
        )
        if fetched_rule is not None:
            reminders_api.update_recurrence_rule(
                fetched_rule,
                interval=3,
                occurrence_count=5,
            )
            linked_fresh, updated_rules = wait_for_relationship_rows(
                "updated recurrence rule to round-trip",
                linked_case.id,
                reminders_api.recurrence_rules_for,
                lambda rows: any(
                    rule.id == fetched_rule.id
                    and rule.interval == 3
                    and rule.occurrence_count == 5
                    for rule in rows
                ),
            )
            tracker.expect(
                any(
                    rule.id == fetched_rule.id
                    and rule.interval == 3
                    and rule.occurrence_count == 5
                    for rule in updated_rules
                ),
                "update_recurrence_rule() updates recurrence fields",
            )
            recurrence_created = next(
                (rule for rule in updated_rules if rule.id == fetched_rule.id),
                fetched_rule,
            )

        banner("8) reminders() + list_reminders() Query Paths")
        expected_created_ids = {r.id for r in state.created.values()}

        visible_in_list = wait_until(
            "created reminders to appear in reminders(list_id=...) output",
            lambda: expected_created_ids.issubset(
                {r.id for r in reminders_api.reminders(list_id=target_list.id)}
            ),
            timeout_seconds=args.consistency_timeout,
            poll_interval=args.poll_interval,
        )
        tracker.expect(
            visible_in_list,
            "reminders(list_id=...) contains all created reminder IDs",
        )

        visible_globally = wait_until(
            "created reminders to appear in reminders() output",
            lambda: expected_created_ids.issubset(
                {r.id for r in reminders_api.reminders()}
            ),
            timeout_seconds=args.consistency_timeout,
            poll_interval=args.poll_interval,
        )
        tracker.expect(
            visible_globally,
            "reminders() contains all created reminder IDs",
        )

        compound_open = reminders_api.list_reminders(
            target_list.id,
            include_completed=False,
            results_limit=args.results_limit,
        )
        compound_all = reminders_api.list_reminders(
            target_list.id,
            include_completed=True,
            results_limit=args.results_limit,
        )

        for key in [
            "reminders",
            "alarms",
            "triggers",
            "attachments",
            "hashtags",
            "recurrence_rules",
        ]:
            tracker.expect(
                hasattr(compound_open, key),
                f"list_reminders(include_completed=False) returns key '{key}'",
            )
            tracker.expect(
                hasattr(compound_all, key),
                f"list_reminders(include_completed=True) returns key '{key}'",
            )

        all_ids_from_compound = {r.id for r in compound_all.reminders}
        tracker.expect(
            expected_created_ids.issubset(all_ids_from_compound),
            "list_reminders(include_completed=True) contains all created reminders",
            f"missing={sorted(expected_created_ids - all_ids_from_compound)}",
        )

        tracker.expect(
            len(compound_all.reminders) >= len(compound_open.reminders),
            "include_completed=True returns at least as many reminders as include_completed=False",
            f"false={len(compound_open.reminders)}, true={len(compound_all.reminders)}",
        )

        tracker.expect(
            arrive_alarm.id in compound_all.alarms,
            "Compound query exposes ARRIVING alarm",
            f"alarm_id={arrive_alarm.id}",
        )
        tracker.expect(
            leave_alarm.id in compound_all.alarms,
            "Compound query exposes LEAVING alarm",
            f"alarm_id={leave_alarm.id}",
        )
        tracker.expect(
            arrive_trigger.id in compound_all.triggers,
            "Compound query exposes ARRIVING trigger",
            f"trigger_id={arrive_trigger.id}",
        )
        tracker.expect(
            leave_trigger.id in compound_all.triggers,
            "Compound query exposes LEAVING trigger",
            f"trigger_id={leave_trigger.id}",
        )
        tracker.expect(
            hashtag_created.id in compound_all.hashtags,
            "Compound query exposes created hashtag",
            f"hashtag_id={hashtag_created.id}",
        )
        tracker.expect(
            attachment_created.id in compound_all.attachments,
            "Compound query exposes created attachment",
            f"attachment_id={attachment_created.id}",
        )
        tracker.expect(
            recurrence_created.id in compound_all.recurrence_rules,
            "Compound query exposes created recurrence rule",
            f"recurrence_rule_id={recurrence_created.id}",
        )

        banner("9) delete() Verification")
        reminders_api.delete(reminders_api.get(delete_candidate.id))
        state.deleted_ids.add(delete_candidate.id)

        deleted_state, delete_missing = wait_for_reminder(
            "deleted reminder to disappear or report deleted=True",
            delete_candidate.id,
            lambda fresh: fresh.deleted is True,
            allow_missing=True,
        )
        if delete_missing:
            tracker.expect(
                True,
                "delete() made reminder non-retrievable via get()",
            )
        else:
            if deleted_state is None:
                deleted_state = reminders_api.get(delete_candidate.id)
            tracker.expect(
                deleted_state.deleted is True,
                "delete() marks reminder as deleted when record remains queryable",
                f"deleted={deleted_state.deleted}",
            )

        linked_fresh = reminders_api.get(linked_case.id)
        reminders_api.delete_hashtag(linked_fresh, hashtag_created)
        linked_fresh = wait_for_linked_id(
            "deleted hashtag ID to disappear from linked reminder",
            linked_case.id,
            "hashtag_ids",
            hashtag_created.id,
            present=False,
        )
        tracker.expect(
            all(
                hid != hashtag_created.id.split("/", 1)[1]
                for hid in linked_fresh.hashtag_ids
            ),
            "delete_hashtag() removes hashtag ID from reminder",
            f"hashtag_ids={linked_fresh.hashtag_ids}",
        )

        reminders_api.delete_attachment(linked_fresh, attachment_created)
        linked_fresh = wait_for_linked_id(
            "deleted attachment ID to disappear from linked reminder",
            linked_case.id,
            "attachment_ids",
            attachment_created.id,
            present=False,
        )
        tracker.expect(
            all(
                aid != attachment_created.id.split("/", 1)[1]
                for aid in linked_fresh.attachment_ids
            ),
            "delete_attachment() removes attachment ID from reminder",
            f"attachment_ids={linked_fresh.attachment_ids}",
        )

        reminders_api.delete_recurrence_rule(linked_fresh, recurrence_created)
        linked_fresh = wait_for_linked_id(
            "deleted recurrence rule ID to disappear from linked reminder",
            linked_case.id,
            "recurrence_rule_ids",
            recurrence_created.id,
            present=False,
        )
        tracker.expect(
            all(
                rid != recurrence_created.id.split("/", 1)[1]
                for rid in linked_fresh.recurrence_rule_ids
            ),
            "delete_recurrence_rule() removes recurrence ID from reminder",
            f"recurrence_rule_ids={linked_fresh.recurrence_rule_ids}",
        )

        banner("Coverage Notes")
        print(
            "Validated snapshot/read/write capabilities in current service implementation:"
        )
        print("  - CRUD for reminders (create/get/update/delete)")
        print("  - Alarm triggers (Location ARRIVING/LEAVING)")
        print("  - Hashtag create/delete")
        print(
            "    update_hashtag() is not live-validated because Hashtag.Name is read-only"
        )
        print("  - URL attachment create/update/delete")
        print("  - Recurrence rule create/update/delete")
        print("  - Query APIs (lists, reminders, list_reminders)")
        print("  - Delta APIs are validated separately by example_reminders_delta.py")
        print(
            "  - Linked fetch helpers "
            "(alarms_for, tags_for, attachments_for, recurrence_rules_for)"
        )

    except Exception as exc:  # pragma: no cover - live integration path
        banner("Fatal Error")
        print(str(exc))
        if args.debug:
            traceback.print_exc()
        return 1

    finally:
        # Cleanup always runs if requested, even after failures.
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
    print(
        "Generated reminders kept in iCloud. "
        "Use --cleanup on the next run if you want auto-deletion."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
