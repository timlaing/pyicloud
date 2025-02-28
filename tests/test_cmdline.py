"""Cmdline tests."""

import argparse
import pickle
from io import BytesIO
from typing import Callable
from unittest import TestCase
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pyicloud import cmdline
from pyicloud.cmdline import (
    _create_parser,
    _display_device_message_option,
    _display_device_silent_message_option,
    _enable_lost_mode_option,
    _handle_2fa,
    _handle_2sa,
    _list_devices_option,
    _play_device_sound_option,
    create_pickled_data,
)

from . import PyiCloudServiceMock
from .const import AUTHENTICATED_USER, REQUIRES_2FA_USER, VALID_2FA_CODE, VALID_PASSWORD
from .const_findmyiphone import FMI_FAMILY_WORKING

# Dictionary to store written data
written_data = {}


# Custom side effect function for open
def mock_file_open(filepath, mode="r", *args, **kwargs):
    if "w" in mode or "a" in mode:
        # Writing or appending mode
        def mock_write(content):
            if filepath not in written_data:
                written_data[filepath] = ""
            if "a" in mode:  # Append mode
                written_data[filepath] += content
            else:  # Write mode
                written_data[filepath] = content

        mock_file = mock_open().return_value
        mock_file.write = mock_write
        return mock_file
    elif "r" in mode:
        raise FileNotFoundError(f"No such file or directory: '{filepath}'")
    else:
        raise ValueError(f"Unsupported mode: {mode}")


class TestCmdline(TestCase):
    """Cmdline test cases."""

    def setUp(self):
        """Set up tests."""
        cmdline.PyiCloudService = PyiCloudServiceMock
        self.main: Callable = cmdline.main
        written_data.clear()

    def test_no_arg(self):
        """Test no args."""
        with pytest.raises(SystemExit, match="2"):
            self.main()

        with pytest.raises(SystemExit, match="2"):
            self.main(None)

        with pytest.raises(SystemExit, match="2"):
            self.main([])

    def test_help(self):
        """Test the help command."""
        with pytest.raises(SystemExit, match="0"):
            self.main(
                [
                    "--help",
                ]
            )

    def test_username(self):
        """Test the username command."""
        # No username supplied
        with pytest.raises(SystemExit, match="2"):
            self.main(
                [
                    "--username",
                ]
            )

    @patch("builtins.open", new_callable=mock_open)
    @patch("keyring.get_password", return_value=None)
    @patch("getpass.getpass")
    def test_username_password_invalid(
        self, mock_getpass, mock_get_password, mock_open
    ):  # pylint: disable=unused-argument
        """Test username and password commands."""
        # No password supplied
        mock_getpass.return_value = None
        with pytest.raises(SystemExit, match="2"):
            self.main(
                [
                    "--username",
                    "invalid_user",
                ]
            )

        # Bad username or password
        mock_getpass.return_value = "invalid_pass"
        with pytest.raises(
            RuntimeError, match="Bad username or password for invalid_user"
        ):
            self.main(
                [
                    "--username",
                    "invalid_user",
                ]
            )

        # We should not use getpass for this one, but we reset the password at login fail
        with pytest.raises(
            RuntimeError, match="Bad username or password for invalid_user"
        ):
            self.main(
                [
                    "--username",
                    "invalid_user",
                    "--password",
                    "invalid_pass",
                ]
            )

    @patch("builtins.open", new_callable=mock_open)
    @patch("keyring.get_password", return_value=None)
    @patch("pyicloud.cmdline.input")
    def test_username_password_requires_2fa(
        self, mock_input, mock_get_password, mock_open
    ):  # pylint: disable=unused-argument
        """Test username and password commands."""
        # Valid connection for the first time
        mock_input.return_value = VALID_2FA_CODE
        with self.assertRaises(SystemExit):
            self.main(
                [
                    "--username",
                    REQUIRES_2FA_USER,
                    "--password",
                    VALID_PASSWORD,
                    "--non-interactive",
                ]
            )

    @patch("keyring.get_password", return_value=None)
    def test_device_outputfile(self, mock_get_password):  # pylint: disable=unused-argument
        """Test the outputfile command."""

        with patch("builtins.open", mock_file_open):
            with self.assertRaises(SystemExit):
                self.main(
                    [
                        "--username",
                        AUTHENTICATED_USER,
                        "--password",
                        VALID_PASSWORD,
                        "--non-interactive",
                        "--outputfile",
                    ]
                )

            devices = FMI_FAMILY_WORKING.get("content")
            if devices:
                for device in devices:
                    file_name = device.get("name").strip().lower() + ".fmip_snapshot"
                    self.assertIn(file_name, written_data)
                    buffer = BytesIO(written_data[file_name])

                    contents = []
                    while True:
                        try:
                            contents.append(pickle.load(buffer))
                        except EOFError:
                            break
                    assert contents == [device]

    @patch("builtins.open", new_callable=mock_open)
    @patch("pickle.dump")
    def test_create_pickled_data(self, mock_pickle_dump, mock_file):
        idevice = MagicMock()
        idevice.content = {"key": "value"}
        filename = "test.pkl"
        create_pickled_data(idevice, filename)
        mock_file.assert_called_with(filename, "wb")
        mock_pickle_dump.assert_called_with(
            idevice.content, mock_file(), protocol=pickle.HIGHEST_PROTOCOL
        )

    def test_create_parser(self):
        parser = _create_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

    def test_enable_lost_mode_option(self):
        command_line = MagicMock(
            lostmode=True,
            device_id="123",
            lost_phone="1234567890",
            lost_message="Lost",
            lost_password="pass",
        )
        dev = MagicMock()
        _enable_lost_mode_option(command_line, dev)
        dev.lost_device.assert_called_with(
            number="1234567890", text="Lost", newpasscode="pass"
        )

    def test_display_device_message_option(self):
        command_line = MagicMock(message="Test Message", device_id="123")
        dev = MagicMock()
        _display_device_message_option(command_line, dev)
        dev.display_message.assert_called_with(
            subject="A Message", message="Test Message", sounds=True
        )

    def test_display_device_silent_message_option(self):
        command_line = MagicMock(silentmessage="Silent Message", device_id="123")
        dev = MagicMock()
        _display_device_silent_message_option(command_line, dev)
        dev.display_message.assert_called_with(
            subject="A Silent Message", message="Silent Message", sounds=False
        )

    def test_play_device_sound_option(self):
        command_line = MagicMock(sound=True, device_id="123")
        dev = MagicMock()
        _play_device_sound_option(command_line, dev)
        dev.play_sound.assert_called_once()

    @patch("pyicloud.cmdline.input", side_effect=["0", "123456"])
    @patch("pyicloud.cmdline._show_devices")
    def test_handle_2sa(self, mock_show_devices, mock_input):
        api = MagicMock()
        mock_show_devices.return_value = [{"deviceName": "Test Device"}]
        api.send_verification_code.return_value = True
        api.validate_verification_code.return_value = True

        _handle_2sa(api)

        api.send_verification_code.assert_called_once_with(
            {"deviceName": "Test Device"}
        )
        api.validate_verification_code.assert_called_once_with(
            {"deviceName": "Test Device"}, "123456"
        )

    @patch("pyicloud.cmdline.input", return_value="123456")
    def test_handle_2fa(self, mock_input):
        api = MagicMock()
        api.validate_2fa_code.return_value = True

        _handle_2fa(api)

        api.validate_2fa_code.assert_called_once_with("123456")

    def test_list_devices_option_locate(self):
        # Create a mock command_line object with the locate option enabled
        command_line = MagicMock(
            locate=True,  # Enable the locate option
            longlist=False,
            output_to_file=False,
            list=False,
        )

        # Create a mock device object
        dev = MagicMock()

        # Call the function
        _list_devices_option(command_line, dev)

        # Verify that the location() method was called
        dev.location.assert_called_once()

    @patch("pyicloud.cmdline.create_pickled_data")
    def test_list_devices_option(self, mock_create_pickled):
        command_line = MagicMock(
            longlist=True,
            locate=False,
            output_to_file=False,
            list=False,
        )
        dev = MagicMock(
            content={
                "name": "Test Device",
                "deviceDisplayName": "Test Display",
                "location": "Test Location",
                "batteryLevel": "100%",
                "batteryStatus": "Charging",
                "deviceClass": "Phone",
                "deviceModel": "iPhone",
            }
        )

        _list_devices_option(command_line, dev)

        # Verify no pickled data creation
        mock_create_pickled.assert_not_called()

        # Check for proper console output during detailed listing
        with patch("builtins.print") as mock_print:
            _list_devices_option(command_line, dev)
            mock_print.assert_any_call("-" * 30)
            mock_print.assert_any_call("Test Device")
            for key, value in dev.content.items():
                mock_print.assert_any_call(f"{key:>20} - {value}")

    @patch("builtins.print")
    def test_list_devices_option_short_list(self, mock_print):
        # Create a mock command_line object with the list option enabled
        command_line = MagicMock(
            longlist=False,
            locate=False,
            output_to_file=False,
            list=True,  # Enable the short list option
        )

        # Create a mock device with sample content
        dev = MagicMock(
            content={
                "name": "Test Device",
                "deviceDisplayName": "Test Display",
                "location": "Test Location",
                "batteryLevel": "100%",
                "batteryStatus": "Charging",
                "deviceClass": "Phone",
                "deviceModel": "iPhone",
            }
        )

        # Call the function
        _list_devices_option(command_line, dev)

        # Verify the output for short list option
        mock_print.assert_any_call("-" * 30)
        mock_print.assert_any_call("Name           - Test Device")
        mock_print.assert_any_call("Display Name   - Test Display")
        mock_print.assert_any_call("Location       - Test Location")
        mock_print.assert_any_call("Battery Level  - 100%")
        mock_print.assert_any_call("Battery Status - Charging")
        mock_print.assert_any_call("Device Class   - Phone")
        mock_print.assert_any_call("Device Model   - iPhone")
