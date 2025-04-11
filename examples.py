"""End to End System test"""

import argparse
import json
import sys
from typing import Any

import click
from requests import Response

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudServiceUnavailable
from pyicloud.services.calendar import CalendarService

END_LIST = "End List\n"
MAX_DISPLAY = 10


def get_api() -> PyiCloudService:
    """Get the PyiCloud API"""
    parser = argparse.ArgumentParser(description="End to End Test of Services")

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

    args: argparse.Namespace = parser.parse_args()

    api = PyiCloudService(
        apple_id=args.username,
        password=args.password,
        china_mainland=args.china_mainland,
    )

    if api.requires_2fa:
        print("Two-factor authentication required.")
        code: str = input(
            "Enter the code you received of one of your approved devices: "
        )
        result: bool = api.validate_2fa_code(code)
        print("Code validation result: %s" % result)

        if not result:
            print("Failed to verify security code")
            sys.exit(1)

        if not api.is_trusted_session:
            print("Session is not trusted. Requesting trust...")
            result = api.trust_session()
            print("Session trust result %s" % result)

            if not result:
                print(
                    "Failed to request trust. You will likely be prompted for the code again in the coming weeks"
                )
    elif api.requires_2sa:
        print("Two-step authentication required. Your trusted devices are:")

        devices: list[dict[str, Any]] = api.trusted_devices
        for i, device in enumerate(devices):
            print(
                "  %s: %s"
                % (i, device.get("deviceName", "SMS to %s" % device.get("phoneNumber")))
            )

        device_index: int = click.prompt(
            "Which device would you like to use?", default=0
        )
        device: dict[str, Any] = devices[device_index]
        if not api.send_verification_code(device):
            print("Failed to send verification code")
            sys.exit(1)

        code = click.prompt("Please enter validation code")
        if not api.validate_verification_code(device, code):
            print("Failed to verify verification code")
            sys.exit(1)

    return api


def display_devices(api: PyiCloudService) -> None:
    """Display device info"""
    print(f"List of devices ({len(api.devices)}):")
    for idx, device in enumerate(api.devices):
        print(f"\t{idx}: {device}")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)

    print("First device:")
    print(f"\t Name: {api.iphone}")
    print(f"\t Location: {json.dumps(api.iphone.location, indent=4)}\n")


def display_calendars(api: PyiCloudService) -> None:
    """Display calendar info"""
    calendar_service: CalendarService = api.calendar
    calendars = calendar_service.get_calendars(as_objs=True)
    print(f"List of calendars ({len(calendars)}):")
    for idx, calendar in enumerate(calendars):
        print(f"\t{idx}: {calendar.title}")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)


def display_contacts(api: PyiCloudService) -> None:
    """Display contacts info"""
    contacts = api.contacts.all
    if contacts:
        print(f"List of contacts ({len(contacts)}):")
        for idx, contact in enumerate(contacts):
            print(
                f"\t{idx}: {contact.get('firstName') or contact.get('lastName') or contact.get('companyName')}"
            )
            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)
    else:
        print("No contacts found\n")


def display_drive(api: PyiCloudService) -> None:
    """Display drive info"""
    drive_files: list[str] = api.drive.dir()
    print(f"List of files in iCloud Drive root ({len(drive_files)}):")
    for idx, filename in enumerate(drive_files):
        print(f"\t{idx}: {filename} ({api.drive[filename].type})")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)


def display_files(api: PyiCloudService) -> None:
    """Display files info"""
    try:
        files: list[str] = api.files.dir()
        print(f"List of files in iCloud files root ({len(files)}):")
        for idx, filename in enumerate(files):
            print(f"\t{idx}: {filename} ({api.files[filename].type})")
            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)
    except PyiCloudServiceUnavailable as error:
        print(f"Files service not available: {error}\n")


def display_photos(api: PyiCloudService) -> None:
    """Display photo info"""
    print(f"List of photo albums ({len(api.photos.albums)}):")
    for idx, album in enumerate(api.photos.albums):
        print(f"\t{idx}: {album}")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)

    print(f"List of ALL PHOTOS ({len(api.photos.all)}):")
    for idx, photo in enumerate(api.photos.all):
        print(f"\t{idx}: {photo.filename} ({photo.item_type})")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)


def display_videos(api: PyiCloudService) -> None:
    """Display video info"""

    print(f"List of Videos ({len(api.photos.albums['Videos'])}):")
    for idx, photo in enumerate(api.photos.albums["Videos"]):
        print(f"\t{idx}: {photo.filename} ({photo.item_type})")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)


def display_shared_photos(api: PyiCloudService) -> None:
    """Display shared photo info"""

    album = None
    print(f"List of Shared Albums ({len(api.photos.shared_streams)}):")
    for idx, album in enumerate(api.photos.shared_streams):
        print(f"\t{idx}: {album}")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)

    if album and api.photos.shared_streams:
        print(
            f"List of Shared Photos [{album}] ({len(api.photos.shared_streams[album])}):"
        )
        for idx, photo in enumerate(api.photos.shared_streams[album]):
            print(f"\t{idx}: {photo.filename} ({photo.item_type})")

            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)


def display_account(api: PyiCloudService) -> None:
    """Display account info"""
    print(f"Account name: {api.account_name}")
    print(f"Account plan: {json.dumps(api.account.summary_plan, indent=4)}")
    print(f"List of Family Member ({len(api.account.family)}):")
    for idx, member in enumerate(api.account.family):
        print(f"\t{idx}: {member}")
        photo: Response = member.get_photo()
        print(f"\t\tPhoto: {photo}")
        print(f"\t\tPhoto type: {photo.headers['Content-Type']}")
        print(f"\t\tPhoto size: {photo.headers['Content-Length']}")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)


def display_hidemyemail(api: PyiCloudService) -> None:
    """Display Hide My Email info"""
    print(f"List of Hide My Email ({len(api.hidemyemail)}):")
    for idx, email in enumerate(api.hidemyemail):
        print(
            f"\t{idx}: {email['hme']} ({email['domain']}) Active = {email['isActive']}"
        )
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)


def main() -> None:
    """main function"""
    api: PyiCloudService = get_api()
    display_hidemyemail(api)
    display_account(api)
    try:
        display_calendars(api)
    except PyiCloudServiceUnavailable as error:
        print(f"Calendar service not available: {error}\n")
    display_files(api)
    display_devices(api)
    display_contacts(api)
    display_drive(api)
    display_photos(api)
    display_videos(api)
    display_shared_photos(api)


if __name__ == "__main__":
    main()
