"""End to End System test"""

import argparse
import contextlib
import http.client
import json
import logging
import sys
import warnings
from datetime import datetime, timedelta
from typing import Any, List, Optional

import click
import requests
from fido2.hid import CtapHidDevice
from requests import Response
from urllib3.exceptions import InsecureRequestWarning

from pyicloud import PyiCloudService
from pyicloud.exceptions import PyiCloudServiceUnavailable
from pyicloud.services.calendar import CalendarObject, CalendarService

END_LIST = "End List\n"
MAX_DISPLAY = 10

# Set to FALSE to disable SSL verification to use tools like charles, mitmproxy, fiddler, or similiar tools to debug the data sent on the wire.
# Can also use command-line argument --disable-ssl-verify
# This uses code taken from:
# - https://stackoverflow.com/questions/15445981/how-do-i-disable-the-security-certificate-check-in-python-requests
# - https://stackoverflow.com/questions/16337511/log-all-requests-from-the-python-requests-module
ENABLE_SSL_VERIFICATION = True

# Set the log level for HTTP commands
# HTTP_LOG_LEVEL = logging.CRITICAL
HTTP_LOG_LEVEL = logging.ERROR
# HTTP_LOG_LEVEL = logging.WARNING
# HTTP_LOG_LEVEL = logging.INFO
# HTTP_LOG_LEVEL = logging.DEBUG

# Set the log level for other commands
# OTHER_LOG_LEVEL = logging.CRITICAL
OTHER_LOG_LEVEL = logging.ERROR
# OTHER_LOG_LEVEL = logging.WARNING
# OTHER_LOG_LEVEL = logging.INFO
# OTHER_LOG_LEVEL = logging.DEBUG

# Set whether to show debug info for HTTPConnection
HTTPCONNECTION_DEBUG_INFO = False

# Set where you'd like the COOKIES to be stored. Can also use command-line argument --cookie-dir
COOKIE_DIR = ""  # location to store session information

# Other configurable variables
APPLE_USERNAME = ""
APPLE_PASSWORD = ""
CHINA = False


def parse_args() -> argparse.Namespace:
    """Parse command line arguments"""
    global ENABLE_SSL_VERIFICATION, COOKIE_DIR, APPLE_PASSWORD, APPLE_USERNAME, CHINA
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

    if len(sys.argv) == 1:
        parser.print_help(sys.stderr)
        sys.exit(1)

    args = parser.parse_args()

    if not args.username or not args.password:
        parser.error("Both --username and --password are required")
    else:
        APPLE_USERNAME = args.username
        APPLE_PASSWORD = args.password

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

    return args


@contextlib.contextmanager
def configurable_ssl_verification(verify_ssl=True):
    opened_adapters = set()

    def merge_environment_settings_with_config(
        self, url, proxies, stream, verify, cert
    ):
        # Add opened adapters to a set so they can be closed later
        opened_adapters.add(self.get_adapter(url))

        settings = old_merge_environment_settings(
            self, url, proxies, stream, verify, cert
        )

        if not verify_ssl:
            settings["verify"] = False
            # You can also uncomment and use proxies here if needed,
            # proxies = {
            #     "http": "http://127.0.0.1:8888",
            #     "https": "http://127.0.0.1:8888"
            # }
            # settings["proxies"] = proxies

        return settings

    # Temporarily override merge_environment_settings
    requests.Session.merge_environment_settings = merge_environment_settings_with_config

    try:
        # Only catch InsecureRequestWarning if we are disabling SSL verification
        if not verify_ssl:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", InsecureRequestWarning)
                yield
        else:
            yield
    finally:
        # Restore the original merge_environment_settings
        requests.Session.merge_environment_settings = old_merge_environment_settings

        # Close all opened adapters
        for adapter in opened_adapters:
            try:
                adapter.close()
            except Exception:
                pass  # Ignore errors during adapter closing


def httpclient_logging_patch(level=HTTP_LOG_LEVEL):
    """Enable HTTPConnection debug logging to the logging framework"""

    def httpclient_log(*args):
        httpclient_logger.log(level, " ".join(args))

    # mask the print() built-in in the http.client module to use
    # logging instead
    http.client.print = httpclient_log
    # enable debugging
    if HTTPCONNECTION_DEBUG_INFO:
        http.client.HTTPConnection.debuglevel = 1
    else:
        http.client.HTTPConnection.debuglevel = 0


def handle_2fa(api: PyiCloudService) -> None:
    """Handle two-factor authentication"""
    security_key_names: Optional[List[str]] = api.security_key_names

    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
        if security_key_names:
            print(
                f"Security key confirmation is required. "
                f"Please plug in one of the following keys: {', '.join(security_key_names)}"
            )

            fido2_devices: List[CtapHidDevice] = api.fido2_devices

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

    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
        trusted_devices: List[dict[str, Any]] = api.trusted_devices
        for i, device in enumerate(trusted_devices):
            print(
                "  %s: %s"
                % (i, device.get("deviceName", "SMS to %s" % device.get("phoneNumber")))
            )

        device_index: int = click.prompt(
            "Which device would you like to use?", default=0
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
    parse_args()

    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
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
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
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
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
        calendar_service: CalendarService = api.calendar
        calendars: list[CalendarObject] = calendar_service.get_calendars(as_objs=True)
        print(f"List of calendars ({len(calendars)}):")
        for idx, calendar in enumerate(calendars):
            print(f"\t{idx}: {calendar.title}")
            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)

        if calendars:
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
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
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
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
        drive_files: list[str] = api.drive.dir()
        print(f"List of files in iCloud Drive root ({len(drive_files)}):")
        for idx, filename in enumerate(drive_files):
            print(f"\t{idx}: {filename} ({api.drive[filename].type})")
            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)


def display_files(api: PyiCloudService) -> None:
    """Display files info"""
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
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
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
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
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
        print(f"List of Videos ({len(api.photos.albums['Videos'])}):")
        for idx, photo in enumerate(api.photos.albums["Videos"]):
            print(f"\t{idx}: {photo.filename} ({photo.item_type})")
            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)


def display_shared_photos(api: PyiCloudService) -> None:
    """Display shared photo info"""
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
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
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
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
    with configurable_ssl_verification(ENABLE_SSL_VERIFICATION):
        print(f"List of Hide My Email ({len(api.hidemyemail)}):")
        for idx, email in enumerate(api.hidemyemail):
            print(
                f"\t{idx}: {email['hme']} ({email['domain']}) Active = {email['isActive']}"
            )
            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)


def main() -> None:
    global httpclient_logger, old_merge_environment_settings

    # Store the original merge_environment_settings
    old_merge_environment_settings = requests.Session.merge_environment_settings

    # Enable general debug logging
    logging.basicConfig(level=OTHER_LOG_LEVEL)

    # Enable httpclient logging
    httpclient_logger = logging.getLogger("http.client")
    httpclient_logging_patch()

    """main function"""
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


if __name__ == "__main__":
    main()
