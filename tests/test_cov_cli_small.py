"""Branch-coverage tests for the small CLI command modules.

These tests exercise the text and JSON output paths plus the abort/error
branches of the ``hidemyemail``, ``contacts``, ``calendar``, ``drive``,
``account`` and ``devices`` command modules, targeting >90% branch coverage
for issue #394.
"""

# pylint: disable=protected-access

from __future__ import annotations

import json
from unittest.mock import patch

from tests.test_cmdline import (
    TEST_ROOT,
    FakeAPI,
    _invoke,
    _invoke_with_cli_args,
    _plain_output,
    _unique_session_dir,
)


class TestHideMyEmail:
    """Hide My Email command coverage."""

    def test_hidemyemail_list_text_and_json(self) -> None:
        """The list command should render a table in text mode and a JSON array."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "hidemyemail", "list")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "alpha@privaterelay.appleid.com" in output
        assert "Shopping" in output
        assert "alias-1" in output

        json_result = _invoke(fake_api, "hidemyemail", "list", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload == [
            {
                "email": "alpha@privaterelay.appleid.com",
                "label": "Shopping",
                "anonymous_id": "alias-1",
            }
        ]

    def test_hidemyemail_generate_text_and_json(self) -> None:
        """The generate command should print the new alias in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "hidemyemail", "generate")
        assert text_result.exit_code == 0
        assert "generated@privaterelay.appleid.com" in _plain_output(text_result)

        json_result = _invoke(fake_api, "hidemyemail", "generate", output_format="json")
        assert json.loads(json_result.stdout) == {
            "email": "generated@privaterelay.appleid.com"
        }

    def test_hidemyemail_generate_empty_alias_aborts(self) -> None:
        """An empty generate response should abort with a user-facing message."""
        fake_api = FakeAPI()
        with patch.object(fake_api.hidemyemail, "generate", return_value=""):
            result = _invoke(fake_api, "hidemyemail", "generate")

        assert result.exit_code != 0
        assert result.exception is not None
        assert (
            result.exception.args[0]
            == "Hide My Email generate returned an empty alias."
        )

    def test_hidemyemail_reserve_text_and_json(self) -> None:
        """The reserve command should print the reserved id in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(
            fake_api,
            "hidemyemail",
            "reserve",
            "new@privaterelay.appleid.com",
            "Work",
        )
        assert text_result.exit_code == 0
        assert "alias-2" in _plain_output(text_result)

        json_result = _invoke(
            fake_api,
            "hidemyemail",
            "reserve",
            "new@privaterelay.appleid.com",
            "Work",
            output_format="json",
        )
        payload = json.loads(json_result.stdout)
        assert payload["anonymousId"] == "alias-2"
        assert payload["label"] == "Work"

    def test_hidemyemail_reserve_invalid_payload_aborts(self) -> None:
        """An invalid reserve payload should abort with the response echoed."""
        fake_api = FakeAPI()
        with patch.object(fake_api.hidemyemail, "reserve", return_value={}):
            result = _invoke(
                fake_api, "hidemyemail", "reserve", "x@example.com", "Label"
            )

        assert result.exit_code != 0
        assert result.exception is not None
        assert "reserve returned an invalid response" in result.exception.args[0]

    def test_hidemyemail_update_text_and_json(self) -> None:
        """The update command should print the updated id in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(
            fake_api,
            "hidemyemail",
            "update",
            "alias-1",
            "New Label",
            "--note",
            "Renamed",
        )
        assert text_result.exit_code == 0
        assert "Updated alias-1" in _plain_output(text_result)

        json_result = _invoke(
            fake_api,
            "hidemyemail",
            "update",
            "alias-1",
            "New Label",
            output_format="json",
        )
        payload = json.loads(json_result.stdout)
        assert payload["anonymousId"] == "alias-1"
        assert payload["label"] == "New Label"
        assert "note" not in payload

    def test_hidemyemail_deactivate_text_and_json(self) -> None:
        """The deactivate command should print the deactivated id."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "hidemyemail", "deactivate", "alias-1")
        assert text_result.exit_code == 0
        assert "Deactivated alias-1" in _plain_output(text_result)

        json_result = _invoke(
            fake_api, "hidemyemail", "deactivate", "alias-1", output_format="json"
        )
        payload = json.loads(json_result.stdout)
        assert payload["anonymousId"] == "alias-1"
        assert payload["active"] is False

    def test_hidemyemail_reactivate_text_and_json(self) -> None:
        """The reactivate command should print the reactivated id."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "hidemyemail", "reactivate", "alias-1")
        assert text_result.exit_code == 0
        assert "Reactivated alias-1" in _plain_output(text_result)

        json_result = _invoke(
            fake_api, "hidemyemail", "reactivate", "alias-1", output_format="json"
        )
        payload = json.loads(json_result.stdout)
        assert payload["anonymousId"] == "alias-1"
        assert payload["active"] is True

    def test_hidemyemail_delete_text_and_json(self) -> None:
        """The delete command should print the deleted id."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "hidemyemail", "delete", "alias-1")
        assert text_result.exit_code == 0
        assert "Deleted alias-1" in _plain_output(text_result)

        json_result = _invoke(
            fake_api, "hidemyemail", "delete", "alias-1", output_format="json"
        )
        payload = json.loads(json_result.stdout)
        assert payload["anonymousId"] == "alias-1"
        assert payload["deleted"] is True


class TestContacts:
    """Contacts command coverage."""

    def test_contacts_list_text_and_json(self) -> None:
        """The list command should render contacts in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "contacts", "list")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "John" in output
        assert "Appleseed" in output
        assert "+1 555-0100" in output
        assert "john@example.com" in output

        json_result = _invoke(fake_api, "contacts", "list", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload == [
            {
                "first_name": "John",
                "last_name": "Appleseed",
                "phones": ["+1 555-0100"],
                "emails": ["john@example.com"],
            }
        ]

    def test_contacts_list_limit_and_empty(self) -> None:
        """The list command should honor --limit and handle an empty address book."""
        fake_api = FakeAPI()

        limited = _invoke(fake_api, "contacts", "list", "--limit", "1")
        assert limited.exit_code == 0
        assert "John" in _plain_output(limited)

        fake_api.contacts.all = []
        empty = _invoke(fake_api, "contacts", "list", output_format="json")
        assert empty.exit_code == 0
        assert json.loads(empty.stdout) == []

    def test_contacts_me_text_and_json(self) -> None:
        """The me command should print the card and photo URL in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "contacts", "me")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "John Appleseed" in output
        assert "Photo URL: https://example.com/photo.jpg" in output

        json_result = _invoke(fake_api, "contacts", "me", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload["first_name"] == "John"
        assert payload["photo"] == {"url": "https://example.com/photo.jpg"}

    def test_contacts_me_string_photo_url(self) -> None:
        """A string photo value should be printed directly as a URL."""
        fake_api = FakeAPI()
        fake_api.contacts.me.photo = "https://example.com/avatar.png"

        result = _invoke(fake_api, "contacts", "me")
        assert result.exit_code == 0
        assert "Photo URL: https://example.com/avatar.png" in _plain_output(result)

    def test_contacts_me_without_photo(self) -> None:
        """A missing photo should skip the photo URL line."""
        fake_api = FakeAPI()
        fake_api.contacts.me.photo = None

        result = _invoke(fake_api, "contacts", "me")
        assert result.exit_code == 0
        assert "Photo URL" not in _plain_output(result)

    def test_contacts_me_photo_without_url(self) -> None:
        """A photo without a url should skip the photo URL line."""
        fake_api = FakeAPI()
        fake_api.contacts.me.photo = {"note": "no url here"}

        result = _invoke(fake_api, "contacts", "me")
        assert result.exit_code == 0
        assert "Photo URL" not in _plain_output(result)

    def test_contacts_me_missing_card_exits(self) -> None:
        """A missing contact card should exit with a message and code 1."""
        fake_api = FakeAPI()
        fake_api.contacts.me = None

        result = _invoke(fake_api, "contacts", "me")
        assert result.exit_code == 1
        assert "No contact card found." in _plain_output(result)


class TestCalendar:
    """Calendar command coverage."""

    def test_calendar_calendars_text_and_json(self) -> None:
        """The calendars command should render calendars in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "calendar", "calendars")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "Home" in output
        assert "cal-1" in output

        json_result = _invoke(fake_api, "calendar", "calendars", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload == [
            {"guid": "cal-1", "title": "Home", "color": "#fff", "share_type": "owner"}
        ]

    def test_calendar_events_text_and_json(self) -> None:
        """The events command should render events in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "calendar", "events")
        assert text_result.exit_code == 0
        assert "Dentist" in _plain_output(text_result)

        json_result = _invoke(fake_api, "calendar", "events", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload == [
            {
                "guid": "event-1",
                "calendar_guid": "cal-1",
                "title": "Dentist",
                "start": "2026-03-01T09:00:00Z",
                "end": "2026-03-01T10:00:00Z",
            }
        ]

    def test_calendar_events_options_and_filter(self) -> None:
        """Events should accept date ranges, periods, guid filters and limits."""
        fake_api = FakeAPI()

        matched = _invoke(
            fake_api,
            "calendar",
            "events",
            "--from",
            "2026-03-01T09:00:00Z",
            "--to",
            "2026-03-02",
            "--period",
            "month",
            "--calendar-guid",
            "cal-1",
            "--limit",
            "1",
            output_format="json",
        )
        assert matched.exit_code == 0
        assert [row["guid"] for row in json.loads(matched.stdout)] == ["event-1"]

        unmatched = _invoke(
            fake_api,
            "calendar",
            "events",
            "--calendar-guid",
            "other",
            output_format="json",
        )
        assert unmatched.exit_code == 0
        assert json.loads(unmatched.stdout) == []


class TestDrive:
    """Drive command coverage."""

    def test_drive_list_folder_text_and_json(self) -> None:
        """Listing a folder should render its children in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "drive", "list")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "report.txt" in output
        assert "42" in output

        json_result = _invoke(fake_api, "drive", "list", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload[0]["name"] == "report.txt"
        assert payload[0]["type"] == "file"
        assert payload[0]["size"] == 42

    def test_drive_list_file_text_and_json(self) -> None:
        """Listing a file should show a single-item payload in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "drive", "list", "/report.txt")
        assert text_result.exit_code == 0
        assert "report.txt" in _plain_output(text_result)

        json_result = _invoke(
            fake_api, "drive", "list", "/report.txt", output_format="json"
        )
        payload = json.loads(json_result.stdout)
        assert payload["name"] == "report.txt"
        assert payload["type"] == "file"

    def test_drive_list_trash_root(self) -> None:
        """--trash should resolve the path from the trash root."""
        fake_api = FakeAPI()

        result = _invoke(fake_api, "drive", "list", "--trash", output_format="json")
        assert result.exit_code == 0
        assert json.loads(result.stdout) == []

    def test_drive_download_file_text_and_json(self) -> None:
        """Downloading a file should stream it to disk in both formats."""
        fake_api = FakeAPI()
        text_path = TEST_ROOT / "cov-small-drive-text.bin"
        text_result = _invoke(
            fake_api, "drive", "download", "/report.txt", "--output", str(text_path)
        )
        assert text_result.exit_code == 0
        assert text_path.read_bytes() == b"hello"
        assert "cov-small-drive-text.bin" in _plain_output(text_result)

        json_path = TEST_ROOT / "cov-small-drive-json.bin"
        json_result = _invoke(
            fake_api,
            "drive",
            "download",
            "/report.txt",
            "--output",
            str(json_path),
            output_format="json",
        )
        payload = json.loads(json_result.stdout)
        assert payload["name"] == "report.txt"
        assert payload["path"] == str(json_path)

    def test_drive_download_folder_aborts(self) -> None:
        """Downloading a folder should abort with a user-facing message."""
        fake_api = FakeAPI()
        output_path = TEST_ROOT / "cov-small-drive-folder.bin"

        result = _invoke(
            fake_api, "drive", "download", "/", "--output", str(output_path)
        )
        assert result.exit_code != 0
        assert result.exception is not None
        assert result.exception.args[0] == "Only files can be downloaded."


class TestAccount:
    """Account command coverage."""

    def test_account_devices_text_and_json(self) -> None:
        """Account devices should render in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "account", "devices")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "Example iPhone" in output
        assert "iPhone 16 Pro" in output

        json_result = _invoke(fake_api, "account", "devices", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload == [
            {
                "id": "acc-device-1",
                "name": "Example iPhone",
                "model_display_name": "iPhone 16 Pro",
                "device_class": "iPhone",
            }
        ]

    def test_account_family_text_and_json(self) -> None:
        """Account family should render in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "account", "family")
        assert text_result.exit_code == 0
        assert "Jane Doe" in _plain_output(text_result)

        json_result = _invoke(fake_api, "account", "family", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload[0]["full_name"] == "Jane Doe"
        assert payload[0]["apple_id"] == "jane@example.com"
        assert payload[0]["has_parental_privileges"] is True

    def test_account_storage_text_and_json(self) -> None:
        """Account storage should render usage and media breakdown in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "account", "storage")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "Used 10.0% of 1000 bytes." in output
        assert "Photos" in output

        json_result = _invoke(fake_api, "account", "storage", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload["usage"]["used_storage_in_percent"] == 10.0
        assert payload["usage"]["total_storage_in_bytes"] == 1000
        assert payload["usages_by_media"]["photos"]["usage_in_bytes"] == 80


class TestDevices:
    """Find My device command coverage."""

    def test_devices_list_text_and_json(self) -> None:
        """The list command should render devices in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "devices", "list")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "Example iPhone" in output
        assert "device-1" in output

        json_result = _invoke(fake_api, "devices", "list", output_format="json")
        payload = json.loads(json_result.stdout)
        assert payload[0]["id"] == "device-1"
        assert payload[0]["location"] is None

    def test_devices_list_locate_outputs_location(self) -> None:
        """--locate should include the current device location."""
        fake_api = FakeAPI()

        json_result = _invoke(
            fake_api, "devices", "list", "--locate", output_format="json"
        )
        assert json_result.exit_code == 0
        payload = json.loads(json_result.stdout)
        assert payload[0]["location"] == {"latitude": 49.0, "longitude": 6.0}

    def test_devices_show_text_and_json(self) -> None:
        """The show command should render normalized details in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "devices", "show", "device-1")
        assert text_result.exit_code == 0
        output = _plain_output(text_result)
        assert "Example iPhone" in output
        assert "iPhone16,1" in output

        json_result = _invoke(
            fake_api, "devices", "show", "device-1", output_format="json"
        )
        payload = json.loads(json_result.stdout)
        assert payload["id"] == "device-1"
        assert payload["display_name"] == "iPhone"
        assert payload["raw_data"]["id"] == "device-1"

    def test_devices_show_raw_text_and_json(self) -> None:
        """--raw should bypass normalization and print the raw payload."""
        fake_api = FakeAPI()

        raw_json = _invoke(
            fake_api, "devices", "show", "device-1", "--raw", output_format="json"
        )
        payload = json.loads(raw_json.stdout)
        assert payload["id"] == "device-1"
        assert payload["name"] == "Example iPhone"

        raw_text = _invoke(fake_api, "devices", "show", "device-1", "--raw")
        assert raw_text.exit_code == 0
        assert '"id": "device-1"' in _plain_output(raw_text)

    def test_devices_show_matches_display_name(self) -> None:
        """The device argument should accept a display name."""
        fake_api = FakeAPI()

        result = _invoke(fake_api, "devices", "show", "iPhone", output_format="json")
        assert result.exit_code == 0
        assert json.loads(result.stdout)["id"] == "device-1"

    def test_devices_sound_text_and_json(self) -> None:
        """The sound command should alert the device in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(fake_api, "devices", "sound", "device-1")
        assert text_result.exit_code == 0
        assert "Requested sound alert for Example iPhone." in _plain_output(text_result)
        assert fake_api.devices[0].sound_subject == "Find My iPhone Alert"

        json_result = _invoke(
            fake_api,
            "devices",
            "sound",
            "device-1",
            "--subject",
            "Custom",
            output_format="json",
        )
        payload = json.loads(json_result.stdout)
        assert payload["device_id"] == "device-1"
        assert payload["subject"] == "Custom"

    def test_devices_message_text_and_json(self) -> None:
        """The message command should display text on the device in both formats."""
        fake_api = FakeAPI()

        silent_result = _invoke(
            fake_api, "devices", "message", "device-1", "Beep!", "--silent"
        )
        assert silent_result.exit_code == 0
        assert "Requested message for Example iPhone." in _plain_output(silent_result)
        assert fake_api.devices[0].messages == [
            {"subject": "A Message", "message": "Beep!", "sounds": False}
        ]

        json_result = _invoke(
            fake_api,
            "devices",
            "message",
            "device-1",
            "Loud!",
            output_format="json",
        )
        payload = json.loads(json_result.stdout)
        assert payload["device_id"] == "device-1"
        assert payload["silent"] is False
        assert fake_api.devices[0].messages[-1]["sounds"] is True

    def test_devices_lost_mode_text_and_json(self) -> None:
        """Lost mode should activate the device in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(
            fake_api,
            "devices",
            "lost-mode",
            "device-1",
            "--phone",
            "+352 111",
            "--passcode",
            "1234",
        )
        assert text_result.exit_code == 0
        assert "Requested lost mode for Example iPhone." in _plain_output(text_result)
        assert fake_api.devices[0].lost_mode == {
            "number": "+352 111",
            "text": "This iPhone has been lost. Please call me.",
            "newpasscode": "1234",
        }

        json_result = _invoke(
            fake_api, "devices", "lost-mode", "device-1", output_format="json"
        )
        payload = json.loads(json_result.stdout)
        assert payload["device_id"] == "device-1"

    def test_devices_erase_forced_text_and_json(self) -> None:
        """A forced erase should proceed in both formats."""
        fake_api = FakeAPI()

        text_result = _invoke(
            fake_api,
            "devices",
            "erase",
            "device-1",
            "--force",
            "--message",
            "Goodbye",
        )
        assert text_result.exit_code == 0
        assert "Requested remote erase for Example iPhone." in _plain_output(
            text_result
        )
        assert fake_api.devices[0].erase_message == "Goodbye"

        json_result = _invoke(
            fake_api, "devices", "erase", "device-1", "--force", output_format="json"
        )
        payload = json.loads(json_result.stdout)
        assert payload["device_id"] == "device-1"

    def test_devices_erase_confirmed_without_force(self) -> None:
        """An accepted confirmation prompt should proceed without --force."""
        fake_api = FakeAPI()

        with patch("typer.confirm", return_value=True):
            result = _invoke(fake_api, "devices", "erase", "device-1")

        assert result.exit_code == 0
        assert fake_api.devices[0].erase_message is not None

    def test_devices_erase_declined_confirmation_aborts(self) -> None:
        """A declined confirmation prompt should abort before erasing."""
        fake_api = FakeAPI()

        with patch("typer.confirm", return_value=False):
            result = _invoke(fake_api, "devices", "erase", "device-1")

        assert result.exit_code != 0
        assert fake_api.devices[0].erase_message is None

    def test_devices_export_text_and_json(self) -> None:
        """Export should write a JSON file and report the path in both formats."""
        fake_api = FakeAPI()
        text_path = TEST_ROOT / "cov-small-export-text.json"
        text_result = _invoke(
            fake_api, "devices", "export", "device-1", "--output", str(text_path)
        )
        assert text_result.exit_code == 0
        assert "cov-small-export-text.json" in _plain_output(text_result)
        assert '"id": "device-1"' in text_path.read_text(encoding="utf-8")

        json_path = TEST_ROOT / "cov-small-export-json.json"
        json_result = _invoke(
            fake_api,
            "devices",
            "export",
            "device-1",
            "--output",
            str(json_path),
            output_format="json",
        )
        payload = json.loads(json_result.stdout)
        assert payload["device_id"] == "device-1"
        assert payload["raw"] is False

    def test_devices_export_raw_and_normalized_content(self) -> None:
        """Export should honor --raw and --normalized payload selection."""
        fake_api = FakeAPI()

        raw_path = TEST_ROOT / "cov-small-export-raw.json"
        raw_result = _invoke(
            fake_api,
            "devices",
            "export",
            "device-1",
            "--output",
            str(raw_path),
            "--raw",
        )
        assert raw_result.exit_code == 0
        raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
        assert raw_payload["name"] == "Example iPhone"
        assert "raw_data" not in raw_payload

        norm_path = TEST_ROOT / "cov-small-export-norm.json"
        norm_result = _invoke(
            fake_api,
            "devices",
            "export",
            "device-1",
            "--output",
            str(norm_path),
            "--normalized",
        )
        assert norm_result.exit_code == 0
        norm_payload = json.loads(norm_path.read_text(encoding="utf-8"))
        assert norm_payload["id"] == "device-1"
        assert "raw_data" in norm_payload

    def test_devices_export_rejects_raw_and_normalized_together(self) -> None:
        """--raw combined with --normalized should fail at parse time."""
        fake_api = FakeAPI()
        output_path = TEST_ROOT / "cov-small-export-bad.json"

        result = _invoke(
            fake_api,
            "devices",
            "export",
            "device-1",
            "--output",
            str(output_path),
            "--raw",
            "--normalized",
        )
        assert result.exit_code == 2
        assert "Choose either --raw or --normalized" in _plain_output(result)

    def test_devices_watch_unbounded_text_interrupt(self) -> None:
        """Unbounded text watch should print progress and exit 130 on interrupt."""
        session_dir = _unique_session_dir("devices-watch-unbounded")
        fake_api = FakeAPI(username="user@example.com", session_dir=session_dir)

        with patch(
            "pyicloud.cli.commands.devices.time.sleep",
            side_effect=KeyboardInterrupt,
        ):
            result = _invoke_with_cli_args(
                fake_api,
                [
                    "devices",
                    "list",
                    "--username",
                    "user@example.com",
                    "--session-dir",
                    str(session_dir),
                    "--refresh-interval",
                    "60",
                ],
            )

        assert result.exit_code == 130
        output = _plain_output(result)
        assert "Waiting 60s before device listing run 2..." in output
        assert output.count("Devices") == 1
