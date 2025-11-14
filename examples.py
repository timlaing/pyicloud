#!/usr/bin/env python
"""End to End System test"""

import argparse
import http.client
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import patch

import click
from fido2.hid import CtapHidDevice
from requests import Response

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudServiceUnavailable
from pyicloud.services.calendar import CalendarObject, CalendarService
from pyicloud.services.photos import BasePhotoAlbum, PhotoAlbum, PhotoAsset
from pyicloud.ssl_context import configurable_ssl_verification

END_LIST: str = "End List\n"
MAX_DISPLAY: int = 10

# Set to FALSE to disable SSL verification to use tools like charles, mitmproxy, fiddler, or similiar tools to debug the data sent on the wire.
# Can also use command-line argument --disable-ssl-verify
# This uses code taken from:
# - https://stackoverflow.com/questions/15445981/how-do-i-disable-the-security-certificate-check-in-python-requests
# - https://stackoverflow.com/questions/16337511/log-all-requests-from-the-python-requests-module
ENABLE_SSL_VERIFICATION: bool = True

# Set the log level for HTTP commands
HTTP_LOG_LEVEL: int = logging.ERROR

# Set the log level for other commands
OTHER_LOG_LEVEL: int = logging.ERROR

# HTTPConnection parameters
HTTPCONNECTION_DEBUG_INFO: bool = False
HTTP_PROXY: Optional[str] = None
HTTPS_PROXY: Optional[str] = None

# Set where you'd like the COOKIES to be stored. Can also use command-line argument --cookie-dir
COOKIE_DIR: str = ""  # location to store session information

# Other configurable variables
APPLE_USERNAME: str = ""
APPLE_PASSWORD: str = ""
CHINA: bool = False


def parse_args() -> None:
    """Parse command line arguments"""
    global ENABLE_SSL_VERIFICATION, COOKIE_DIR, APPLE_PASSWORD, APPLE_USERNAME, CHINA, HTTP_PROXY, HTTPS_PROXY  # pylint: disable=global-statement
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
        "--cookie-dir",
        action="store",
        dest="cookie_directory",
        default="",
        help="Directory to store session information and cookies",
    )
    parser.add_argument(
        "--china-mainland",
        action="store_true",
        dest="china_mainland",
        default=False,
        help="If the country/region setting of the Apple ID is China mainland",
    )
    parser.add_argument(
        "--disable-ssl-verify",
        action="store_true",
        dest="disable_ssl",
        default=False,
        help="Disable SSL verification",
    )

    parser.add_argument(
        "--http-proxy",
        type=str,
        help="Use HTTP proxy for requests",
    )

    parser.add_argument(
        "--https-proxy",
        type=str,
        help="Use HTTPS proxy for requests",
    )

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args: argparse.Namespace = parser.parse_args()

    if not args.username:
        parser.error("Both --username is required")
    else:
        APPLE_USERNAME = args.username
        APPLE_PASSWORD = args.password or ""

    if args.cookie_directory:
        COOKIE_DIR = args.cookie_directory

    if args.china_mainland:
        CHINA = args.china_mainland

    if args.disable_ssl or not ENABLE_SSL_VERIFICATION:
        ENABLE_SSL_VERIFICATION = False
        print("=" * 80)
        print("⚠️  SECURITY WARNING: SSL VERIFICATION DISABLED ⚠️")
        print("This is insecure and should ONLY be used for debugging!")
        print("Your credentials and data may be exposed to attackers.")
        print("=" * 80)
        print()

    if args.http_proxy:
        HTTP_PROXY = args.http_proxy
    if args.https_proxy:
        HTTPS_PROXY = args.https_proxy


def httpclient_logging_patch(level=HTTP_LOG_LEVEL) -> None:
    """Enable HTTPConnection debug logging to the logging framework"""
    httpclient_logger: logging.Logger = logging.getLogger("http.client")
    httpclient_logger.setLevel(level)

    def httpclient_log(*args) -> None:
        httpclient_logger.log(level, " ".join(map(str, args)))

    # mask the print() built-in in the http.client module to use
    # logging instead
    patch("http.client.print", httpclient_log).start()

    # enable debugging
    if HTTPCONNECTION_DEBUG_INFO:
        http.client.HTTPConnection.debuglevel = 1
    else:
        http.client.HTTPConnection.debuglevel = 0


def handle_2fa(api: PyiCloudService) -> None:
    """Handle two-factor authentication"""
    security_key_names: Optional[List[str]] = api.security_key_names

    if security_key_names:
        print(
            f"Security key confirmation is required. "
            f"Please plug in one of the following keys: {', '.join(security_key_names)}"
        )

        fido2_devices: List[CtapHidDevice] = api.fido2_devices

        if not fido2_devices:
            print("No FIDO2 devices detected. Connect a security key and try again.")
            sys.exit(1)

        print("Available FIDO2 devices:")

        for idx, dev in enumerate(fido2_devices, start=1):
            print(f"{idx}: {dev}")

        choice = click.prompt(
            "Select a FIDO2 device by number",
            type=click.IntRange(1, len(fido2_devices)),
            default=1,
        )
        selected_device: CtapHidDevice = fido2_devices[choice - 1]

        print("Please confirm the action using the security key")

        api.confirm_security_key(selected_device)

    else:
        print("Two-factor authentication required.")
        code: str = input(
            "Enter the code you received of one of your approved devices: "
        )
        result: bool = api.validate_2fa_code(code)
        print(f"Code validation result: {result}")

        if not result:
            print("Failed to verify security code")
            sys.exit(1)

    if not api.is_trusted_session:
        print("Session is not trusted. Requesting trust...")
        result = api.trust_session()
        print(f"Session trust result: {result}")

        if not result:
            print(
                "Failed to request trust. You will likely be prompted for confirmation again in the coming weeks"
            )


def handle_2sa(api: PyiCloudService) -> None:
    """Handle two-step authentication"""
    print("Two-step authentication required. Your trusted devices are:")

    trusted_devices: List[dict[str, Any]] = api.trusted_devices
    if not trusted_devices:
        print("No trusted devices are available for 2-step verification.")
        sys.exit(1)
    for i, device in enumerate(trusted_devices):
        print(
            "  %s: %s"
            % (i, device.get("deviceName", "SMS to %s" % device.get("phoneNumber")))
        )

    device_index: int = click.prompt(
        "Which device would you like to use?",
        type=click.IntRange(0, len(trusted_devices) - 1),
        default=0,
    )
    device: dict[str, Any] = trusted_devices[device_index]
    if not api.send_verification_code(device):
        print("Failed to send verification code")
        sys.exit(1)

    code = click.prompt("Please enter validation code")
    if not api.validate_verification_code(device, code):
        print("Failed to verify verification code")
        sys.exit(1)


def get_api() -> PyiCloudService:
    """Get authenticated PyiCloudService instance"""
    api = PyiCloudService(
        apple_id=APPLE_USERNAME,
        password=APPLE_PASSWORD,
        china_mainland=CHINA,
        cookie_directory=COOKIE_DIR,
    )

    if api.requires_2fa:
        handle_2fa(api)

    elif api.requires_2sa:
        handle_2sa(api)

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
    calendars: list[CalendarObject] = calendar_service.get_calendars(as_objs=True)
    print(f"List of calendars ({len(calendars)}):")
    for idx, calendar in enumerate(calendars):
        print(f"\t{idx}: {calendar.title}")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)

    if not calendars:
        return

    # Get recent events from API
    try:
        recent_events = calendar_service.get_events(
            from_dt=datetime.now() - timedelta(days=7),
            to_dt=datetime.now() + timedelta(days=7),
            as_objs=True,
        )
        print(f"Recent events (±7 days): {len(recent_events)} events")
        for idx, event in enumerate(recent_events):
            if hasattr(event, "title") and hasattr(event, "start_date"):
                print(f"\t{idx}: {event.title} at {event.start_date}")
                if idx >= MAX_DISPLAY - 1:
                    break
        print(END_LIST)
    except Exception as e:
        print(f"Could not retrieve events: {e}\n")


def display_contacts(api: PyiCloudService) -> None:
    """Display contacts info"""

    contacts: List[dict[str, Any]] | None = api.contacts.all
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
            data: bytes | None = photo.download()
            if data:
                print(f"\t\tDownloaded {len(data)} bytes")
            else:
                print("\t\tDownload failed")
            break
    print(END_LIST)


def display_videos(api: PyiCloudService) -> None:
    """Display video info"""
    if "Videos" in api.photos.albums:
        print(f"List of Videos ({len(api.photos.albums['Videos'])}):")
        for idx, photo in enumerate(api.photos.albums["Videos"]):
            print(f"\t{idx}: {photo.filename} ({photo.item_type})")
            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)
    else:
        print("No 'Videos' album found")


def display_shared_photos(api: PyiCloudService) -> None:
    """Display shared photo info"""

    selected_album: BasePhotoAlbum | None = next(iter(api.photos.shared_streams), None)
    print(f"List of Shared Albums ({len(api.photos.shared_streams)}):")
    for idx, album in enumerate(api.photos.shared_streams):
        print(f"\t{idx}: {album.name} ({len(album)} photos)")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)

    if selected_album and api.photos.shared_streams:
        print(f"List of Shared Photos [{selected_album.name}] ({len(selected_album)}):")
        for idx, photo in enumerate(selected_album):
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
        try:
            photo: Response = member.get_photo()
            print(f"\t\tPhoto: {photo}")
            print(f"\t\tPhoto type: {photo.headers['Content-Type']}")
            print(f"\t\tPhoto size: {photo.headers['Content-Length']}")
        except Exception as e:
            print(f"\t\tPhoto: Error retrieving user photo: {e}")
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


def album_management(api: PyiCloudService) -> None:
    """Test album management functions"""

    album_name = "Test Album from API"
    print(f"Creating album '{album_name}'...")
    album: PhotoAlbum | None = api.photos.create_album(album_name)
    print(f"Album created: {album}")
    if album is None:
        print("Album creation failed.")
        return

    print(f"Album '{album_name}' created successfully.")
    album.name = "Renamed Album"
    print(f"Album renamed to '{album.name}'")

    sample_photo: Path = Path(__file__).with_name("sample.jpg")
    if sample_photo.exists():
        photo: PhotoAsset | None = album.upload(str(sample_photo))
        if photo:
            print(f"Photo uploaded successfully: {photo.filename} ({photo.item_type})")
            if photo.delete():
                print("Photo deleted successfully.")
        else:
            print("Photo upload failed.")
    else:
        print(f"Skipping upload: sample photo not found at {sample_photo}")

    print(f"Deleting album '{album.name}'...")
    if album.delete():
        print("Album deleted.")
    else:
        print("Album deletion failed.")


def setup() -> None:
    """Setup"""
    parse_args()

    # Enable general debug logging
    logging.basicConfig(level=OTHER_LOG_LEVEL)

    # Enable httpclient logging
    httpclient_logging_patch()


def main() -> None:
    """main function"""
    with configurable_ssl_verification(
        ENABLE_SSL_VERIFICATION,
        HTTP_PROXY,
        HTTPS_PROXY,
    ):
        api: PyiCloudService = get_api()

        display_account(api)
        display_devices(api)
        display_hidemyemail(api)
        try:
            display_calendars(api)
        except PyiCloudServiceUnavailable as error:
            print(f"Calendar service not available: {error}\n")
        display_files(api)
        display_contacts(api)
        display_drive(api)
        display_photos(api)
        display_videos(api)
        display_shared_photos(api)
        album_management(api)


if __name__ == "__main__":
    setup()
    main()
