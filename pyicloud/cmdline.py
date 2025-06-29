#! /usr/bin/env python
"""
A Command Line Wrapper to allow easy use of pyicloud for
command line scripts, and related.
"""

import argparse
import logging
import os
import pickle
import sys
from pprint import pformat
from typing import Any, Optional

from click import confirm

from pyicloud import PyiCloudService, utils
from pyicloud.exceptions import PyiCloudFailedLoginException
from pyicloud.services.findmyiphone import AppleDevice

DEVICE_ERROR = "Please use the --device switch to indicate which device to use."


def create_pickled_data(idevice: AppleDevice, filename: str) -> None:
    """
    This helper will output the idevice to a pickled file named
    after the passed filename.

    This allows the data to be used without resorting to screen / pipe
    scrapping.
    """
    with open(filename, "wb") as pickle_file:
        pickle.dump(
            idevice.data,
            pickle_file,
            protocol=pickle.HIGHEST_PROTOCOL,
        )


def _create_parser() -> argparse.ArgumentParser:
    """Create the parser."""
    parser = argparse.ArgumentParser(description="Find My iPhone CommandLine Tool")

    parser.add_argument(
        "--username",
        action="store",
        dest="username",
        default="",
        help="Apple ID to Use",
    )
    parser.add_argument(
        "--password",
        action="store",
        dest="password",
        default="",
        help=(
            "Apple ID Password to Use; if unspecified, password will be "
            "fetched from the system keyring."
        ),
    )
    parser.add_argument(
        "--china-mainland",
        action="store_true",
        dest="china_mainland",
        default=False,
        help="If the country/region setting of the Apple ID is China mainland",
    )
    parser.add_argument(
        "-n",
        "--non-interactive",
        action="store_false",
        dest="interactive",
        default=True,
        help="Disable interactive prompts.",
    )
    parser.add_argument(
        "--delete-from-keyring",
        action="store_true",
        dest="delete_from_keyring",
        default=False,
        help="Delete stored password in system keyring for this username.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list",
        default=False,
        help="Short Listings for Device(s) associated with account",
    )
    parser.add_argument(
        "--llist",
        action="store_true",
        dest="longlist",
        default=False,
        help="Detailed Listings for Device(s) associated with account",
    )
    parser.add_argument(
        "--locate",
        action="store_true",
        dest="locate",
        default=False,
        help="Retrieve Location for the iDevice (non-exclusive).",
    )

    # Restrict actions to a specific devices UID / DID
    parser.add_argument(
        "--device",
        action="store",
        dest="device_id",
        default=False,
        help="Only effect this device",
    )

    # Trigger Sound Alert
    parser.add_argument(
        "--sound",
        action="store_true",
        dest="sound",
        default=False,
        help="Play a sound on the device",
    )

    # Trigger Message w/Sound Alert
    parser.add_argument(
        "--message",
        action="store",
        dest="message",
        default=False,
        help="Optional Text Message to display with a sound",
    )

    # Trigger Message (without Sound) Alert
    parser.add_argument(
        "--silentmessage",
        action="store",
        dest="silentmessage",
        default=False,
        help="Optional Text Message to display with no sounds",
    )

    # Lost Mode
    parser.add_argument(
        "--lostmode",
        action="store_true",
        dest="lostmode",
        default=False,
        help="Enable Lost mode for the device",
    )
    parser.add_argument(
        "--lostphone",
        action="store",
        dest="lost_phone",
        default=False,
        help="Phone Number allowed to call when lost mode is enabled",
    )
    parser.add_argument(
        "--lostpassword",
        action="store",
        dest="lost_password",
        default=False,
        help="Forcibly active this passcode on the idevice",
    )
    parser.add_argument(
        "--lostmessage",
        action="store",
        dest="lost_message",
        default="",
        help="Forcibly display this message when activating lost mode.",
    )

    # Output device data to an pickle file
    parser.add_argument(
        "--outputfile",
        action="store_true",
        dest="output_to_file",
        default="",
        help="Save device data to a file in the current directory.",
    )
    parser.add_argument(
        "--log-level",
        action="store",
        dest="loglevel",
        choices=["error", "warning", "info", "none"],
        default="info",
        help="Set the logging level",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    return parser


def _get_password(
    username: str,
    parser: argparse.ArgumentParser,
    command_line: argparse.Namespace,
) -> Optional[str]:
    """Which password we use is determined by your username, so we
    do need to check for this first and separately."""
    if not username:
        parser.error("No username supplied")

    password: Optional[str] = command_line.password
    if not password:
        password = utils.get_password(username, interactive=command_line.interactive)

    return password


def main() -> None:
    """Main commandline entrypoint."""
    parser: argparse.ArgumentParser = _create_parser()
    command_line: argparse.Namespace = parser.parse_args()
    level = logging.INFO

    if command_line.loglevel == "error":
        level = logging.ERROR
    elif command_line.loglevel == "warning":
        level = logging.WARNING
    elif command_line.loglevel == "info":
        level = logging.INFO
    elif command_line.loglevel == "none":
        level = None

    if command_line.debug:
        level = logging.DEBUG

    if level:
        logging.basicConfig(level=level)

    username: str = command_line.username.strip()
    china_mainland: bool = command_line.china_mainland

    if username and command_line.delete_from_keyring:
        utils.delete_password_in_keyring(username)

    failure_count = 0
    while True:
        password: Optional[str] = _get_password(username, parser, command_line)

        api: Optional[PyiCloudService] = _authenticate(
            username,
            password,
            china_mainland,
            parser,
            command_line,
            failures=failure_count,
        )
        if not api:
            failure_count += 1
        else:
            break

    _print_devices(api, command_line)


def _authenticate(
    username: str,
    password: Optional[str],
    china_mainland: bool,
    parser: argparse.ArgumentParser,
    command_line: argparse.Namespace,
    failures: int = 0,
) -> Optional[PyiCloudService]:
    api = None
    try:
        api = PyiCloudService(username, password, china_mainland=china_mainland)
        if (
            not utils.password_exists_in_keyring(username)
            and command_line.interactive
            and confirm("Save password in keyring?")
            and password
        ):
            utils.store_password_in_keyring(username, password)

        if api.requires_2fa:
            _handle_2fa(api)

        elif api.requires_2sa:
            _handle_2sa(api)
        return api
    except PyiCloudFailedLoginException as err:
        # If they have a stored password; we just used it and
        # it did not work; let's delete it if there is one.
        if not password:
            parser.error("No password supplied")

        if utils.password_exists_in_keyring(username):
            utils.delete_password_in_keyring(username)

        message: str = f"Bad username or password for {username}"

        failures += 1
        if failures >= 3:
            raise RuntimeError(message) from err

        print(message, file=sys.stderr)


def _print_devices(api: PyiCloudService, command_line: argparse.Namespace) -> None:
    for dev in api.devices:
        if not command_line.device_id or (
            command_line.device_id.strip().lower() == dev.id.strip().lower()
        ):
            # List device(s)
            _list_devices_option(command_line, dev)

            # Play a Sound on a device
            _play_device_sound_option(command_line, dev)

            # Display a Message on the device
            _display_device_message_option(command_line, dev)

            # Display a Silent Message on the device
            _display_device_silent_message_option(command_line, dev)

            # Enable Lost mode
            _enable_lost_mode_option(command_line, dev)


def _enable_lost_mode_option(
    command_line: argparse.Namespace, dev: AppleDevice
) -> None:
    if command_line.lostmode:
        if command_line.device_id:
            dev.lost_device(
                number=command_line.lost_phone.strip(),
                text=command_line.lost_message.strip(),
                newpasscode=command_line.lost_password.strip(),
            )
        else:
            raise RuntimeError(
                f"Lost Mode can only be activated on a singular device. {DEVICE_ERROR}"
            )


def _display_device_silent_message_option(
    command_line: argparse.Namespace, dev: AppleDevice
) -> None:
    if command_line.silentmessage:
        if command_line.device_id:
            dev.display_message(
                subject="A Silent Message",
                message=command_line.silentmessage,
                sounds=False,
            )
        else:
            raise RuntimeError(
                f"Silent Messages can only be played on a singular device. {DEVICE_ERROR}"
            )


def _display_device_message_option(
    command_line: argparse.Namespace, dev: AppleDevice
) -> None:
    if command_line.message:
        if command_line.device_id:
            dev.display_message(
                subject="A Message", message=command_line.message, sounds=True
            )
        else:
            raise RuntimeError(
                f"Messages can only be played on a singular device. {DEVICE_ERROR}"
            )


def _play_device_sound_option(
    command_line: argparse.Namespace, dev: AppleDevice
) -> None:
    if command_line.sound:
        if command_line.device_id:
            dev.play_sound()
        else:
            raise RuntimeError(
                f"\n\n\t\tSounds can only be played on a singular device. {DEVICE_ERROR}\n\n"
            )


def _list_devices_option(command_line: argparse.Namespace, dev: AppleDevice) -> None:
    location = dev.location if command_line.locate else None

    if command_line.output_to_file:
        create_pickled_data(
            dev,
            filename=(dev.name.strip().lower() + ".fmip_snapshot"),
        )

    if command_line.longlist:
        print("-" * 30)
        print(dev.name)
        for key in dev.data:
            print(
                f"{key:>30} - {pformat(dev.data[key]).replace(os.linesep, os.linesep + ' ' * 33)}"
            )
    elif command_line.list:
        print("-" * 30)
        print(f"Name           - {dev.name}")
        print(f"Display Name   - {dev.deviceDisplayName}")
        print(f"Location       - {location or dev.location}")
        print(f"Battery Level  - {dev.batteryLevel}")
        print(f"Battery Status - {dev.batteryStatus}")
        print(f"Device Class   - {dev.deviceClass}")
        print(f"Device Model   - {dev.deviceModel}")


def _handle_2fa(api: PyiCloudService) -> None:
    print("\nTwo-step authentication required.", "\nPlease enter validation code")

    code: str = input("(string) --> ")
    if not api.validate_2fa_code(code):
        print("Failed to verify verification code")
        sys.exit(1)

    print("")


def _handle_2sa(api: PyiCloudService) -> None:
    print("\nTwo-step authentication required.", "\nYour trusted devices are:")

    devices: list[dict[str, Any]] = _show_devices(api)

    print("\nWhich device would you like to use?")
    device_idx = int(input("(number) --> "))
    device: dict[str, Any] = devices[device_idx]
    if not api.send_verification_code(device):
        print("Failed to send verification code")
        sys.exit(1)

    print("\nPlease enter validation code")
    code: str = input("(string) --> ")
    if not api.validate_verification_code(device, code):
        print("Failed to verify verification code")
        sys.exit(1)

    print("")


def _show_devices(api: PyiCloudService) -> list[dict[str, Any]]:
    """Show devices."""
    devices: list[dict[str, Any]] = api.trusted_devices
    for i, device in enumerate(devices):
        phone_number: str = f"SMS to {device.get('phoneNumber')}"
        print(f"    {i}: {device.get('deviceName', phone_number)}")

    return devices


if __name__ == "__main__":
    main()
