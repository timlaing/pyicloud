"""Cmdline tests."""
# pylint: disable=protected-access

import argparse
import pickle
from io import BytesIO
from pprint import pformat
from typing import Any
from unittest.mock import MagicMock, PropertyMock, mock_open, patch

import pytest

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
    main,
)
from pyicloud.services.findmyiphone import AppleDevice
from tests import PyiCloudSessionMock
from tests.const import (
    AUTHENTICATED_USER,
    REQUIRES_2FA_USER,
    VALID_2FA_CODE,
    VALID_PASSWORD,
)
from tests.const_findmyiphone import FMI_FAMILY_WORKING

# Dictionary to store written data
written_data: dict[str, Any] = {}


# Custom side effect function for open
def mock_file_open(filepath: str, mode="r", **_):
    """Mock file open function."""
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


def test_no_arg() -> None:
    """Test no args."""
    with pytest.raises(SystemExit, match="2"):
        main()


def test_username_password_invalid() -> None:
    """Test username and password commands."""
    # No password supplied
    with (
        patch("getpass.getpass", return_value=None),
        patch("argparse.ArgumentParser.parse_args") as mock_parse_args,
        patch("builtins.open", new_callable=mock_open),
        patch("pyicloud.base.PyiCloudSession", new=PyiCloudSessionMock),
        pytest.raises(SystemExit, match="2"),
    ):
        mock_parse_args.return_value = argparse.Namespace(
            username="valid_user",
            password=None,
            debug=False,
            interactive=True,
            china_mainland=False,
            delete_from_keyring=False,
            loglevel="info",
        )
        main()

    # Bad username or password
    with (
        patch("getpass.getpass", return_value="invalid_pass"),
        patch("argparse.ArgumentParser.parse_args") as mock_parse_args,
        patch("builtins.open", new_callable=mock_open),
        patch("pyicloud.base.PyiCloudSession", new=PyiCloudSessionMock),
        pytest.raises(RuntimeError, match="Bad username or password for invalid_user"),
    ):
        mock_parse_args.return_value = argparse.Namespace(
            username="invalid_user",
            password=None,
            debug=False,
            interactive=True,
            china_mainland=False,
            delete_from_keyring=False,
            loglevel="error",
        )
        main()

    # We should not use getpass for this one, but we reset the password at login fail
    with (
        patch("argparse.ArgumentParser.parse_args") as mock_parse_args,
        patch("builtins.open", new_callable=mock_open),
        patch("pyicloud.base.PyiCloudSession", new=PyiCloudSessionMock),
        pytest.raises(RuntimeError, match="Bad username or password for invalid_user"),
    ):
        mock_parse_args.return_value = argparse.Namespace(
            username="invalid_user",
            password="invalid_pass",
            debug=False,
            interactive=False,
            china_mainland=False,
            delete_from_keyring=False,
            loglevel="warning",
        )
        main()


def test_username_password_requires_2fa() -> None:
    """Test username and password commands."""
    # Valid connection for the first time
    with (
        patch("argparse.ArgumentParser.parse_args") as mock_parse_args,
        patch("pyicloud.cmdline.input", return_value=VALID_2FA_CODE),
        patch("pyicloud.cmdline.confirm", return_value=False),
        patch("keyring.get_password", return_value=None),
        patch("builtins.open", new_callable=mock_open),
        patch("pyicloud.base.PyiCloudSession", new=PyiCloudSessionMock),
    ):
        mock_parse_args.return_value = argparse.Namespace(
            username=REQUIRES_2FA_USER,
            password=VALID_PASSWORD,
            debug=False,
            interactive=True,
            china_mainland=False,
            delete_from_keyring=False,
            device_id=None,
            locate=None,
            output_to_file=None,
            longlist=None,
            list=None,
            sound=None,
            message=None,
            silentmessage=None,
            lostmode=None,
            loglevel="warning",
        )
        main()


def test_device_outputfile() -> None:
    """Test the outputfile command."""

    with (
        patch("argparse.ArgumentParser.parse_args") as mock_parse_args,
        patch("builtins.open", mock_file_open),
        patch("keyring.get_password", return_value=None),
        patch("pyicloud.base.PyiCloudSession", new=PyiCloudSessionMock),
    ):
        mock_parse_args.return_value = argparse.Namespace(
            username=AUTHENTICATED_USER,
            password=VALID_PASSWORD,
            debug=False,
            interactive=False,
            china_mainland=False,
            delete_from_keyring=False,
            device_id=None,
            locate=None,
            output_to_file=True,
            longlist=None,
            list=None,
            sound=None,
            message=None,
            silentmessage=None,
            lostmode=None,
            loglevel="none",
        )
        main()

        devices = FMI_FAMILY_WORKING.get("content")
        if devices:
            for device in devices:
                file_name = device.get("name").strip().lower() + ".fmip_snapshot"
                assert file_name in written_data
                buffer = BytesIO(written_data[file_name])

                contents = []
                while True:
                    try:
                        contents.append(pickle.load(buffer))
                    except EOFError:
                        break
                assert contents == [device]


def test_create_pickled_data() -> None:
    """Test the creation of pickled data."""
    idevice = MagicMock()
    idevice.data = {"key": "value"}
    filename = "test.pkl"
    with (
        patch("builtins.open", new_callable=mock_open) as mock_file,
        patch("pickle.dump") as mock_pickle_dump,
        patch("pyicloud.base.PyiCloudSession", new=PyiCloudSessionMock),
    ):
        create_pickled_data(idevice, filename)
        mock_file.assert_called_with(filename, "wb")
        mock_pickle_dump.assert_called_with(
            idevice.data, mock_file(), protocol=pickle.HIGHEST_PROTOCOL
        )


def test_create_parser() -> None:
    """Test the creation of the parser."""
    parser: argparse.ArgumentParser = _create_parser()
    assert isinstance(parser, argparse.ArgumentParser)


def test_enable_lost_mode_option() -> None:
    """Test the enable lost mode option."""
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


def test_display_device_message_option() -> None:
    """Test the display device message option."""
    command_line = MagicMock(message="Test Message", device_id="123")
    dev = MagicMock()
    _display_device_message_option(command_line, dev)
    dev.display_message.assert_called_with(
        subject="A Message", message="Test Message", sounds=True
    )


def test_display_device_silent_message_option() -> None:
    """Test the display device silent message option."""
    command_line = MagicMock(silentmessage="Silent Message", device_id="123")
    dev = MagicMock()
    _display_device_silent_message_option(command_line, dev)
    dev.display_message.assert_called_with(
        subject="A Silent Message", message="Silent Message", sounds=False
    )


def test_play_device_sound_option() -> None:
    """Test the play device sound option."""
    command_line = MagicMock(sound=True, device_id="123")
    dev = MagicMock()
    _play_device_sound_option(command_line, dev)
    dev.play_sound.assert_called_once()


def test_handle_2sa() -> None:
    """Test the handle 2sa function."""
    api = MagicMock()
    api.send_verification_code.return_value = True
    api.validate_verification_code.return_value = True
    with (
        patch("pyicloud.cmdline.input", side_effect=["0", "123456"]),
        patch(
            "pyicloud.cmdline._show_devices",
            return_value=[{"deviceName": "Test Device"}],
        ),
    ):
        _handle_2sa(api)

        api.send_verification_code.assert_called_once_with(
            {"deviceName": "Test Device"}
        )
        api.validate_verification_code.assert_called_once_with(
            {"deviceName": "Test Device"},
            "123456",
        )


def test_handle_2fa() -> None:
    """Test the handle 2fa function."""
    api = MagicMock()
    api.validate_2fa_code.return_value = True
    with patch("pyicloud.cmdline.input", return_value="123456"):
        _handle_2fa(api)
        api.validate_2fa_code.assert_called_once_with("123456")


def test_list_devices_option_locate() -> None:
    """Test the list devices option with locate."""
    # Create a mock command_line object with the locate option enabled
    command_line = MagicMock(
        locate=True,  # Enable the locate option
        longlist=False,
        output_to_file=False,
        list=False,
    )

    # Create a mock device object

    dev = MagicMock()
    location = PropertyMock(return_value="Test Location")
    type(dev).location = location

    # Call the function
    _list_devices_option(command_line, dev)

    # Verify that the location() method was called
    location.assert_called_once()


def test_list_devices_option() -> None:
    """Test the list devices option."""
    command_line = MagicMock(
        longlist=True,
        locate=False,
        output_to_file=False,
        list=False,
    )
    content: dict[str, str] = {
        "name": "Test Device",
        "deviceDisplayName": "Test Display",
        "location": "Test Location",
        "batteryLevel": "100%",
        "batteryStatus": "Charging",
        "deviceClass": "Phone",
        "deviceModel": "iPhone",
    }
    dev = AppleDevice(
        content=content,
        params={},
        manager=MagicMock(),
        sound_url="",
        lost_url="",
        message_url="",
        erase_token_url="",
        erase_url="",
    )

    with patch("pyicloud.cmdline.create_pickled_data") as mock_create_pickled:
        _list_devices_option(command_line, dev)

        # Verify no pickled data creation
        mock_create_pickled.assert_not_called()

    # Check for proper console output during detailed listing
    with patch("builtins.print") as mock_print:
        _list_devices_option(command_line, dev)
        mock_print.assert_any_call("-" * 30)
        mock_print.assert_any_call("Test Device")
        for key, value in content.items():
            mock_print.assert_any_call(f"{key:>30} - {pformat(value)}")


def test_list_devices_option_short_list() -> None:
    """Test the list devices option with short list."""
    # Create a mock command_line object with the list option enabled
    command_line = MagicMock(
        longlist=False,
        locate=False,
        output_to_file=False,
        list=True,  # Enable the short list option
    )

    # Create a mock device with sample content
    content: dict[str, str] = {
        "name": "Test Device",
        "deviceDisplayName": "Test Display",
        "location": "Test Location",
        "batteryLevel": "100%",
        "batteryStatus": "Charging",
        "deviceClass": "Phone",
        "deviceModel": "iPhone",
    }
    dev = AppleDevice(
        content=content,
        params={},
        manager=MagicMock(),
        sound_url="",
        lost_url="",
        message_url="",
        erase_token_url="",
        erase_url="",
    )

    with patch("builtins.print") as mock_print:
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
