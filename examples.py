"""End to End System test"""

import argparse

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

    return PyiCloudService(
        apple_id=args.username,
        password=args.password,
        china_mainland=args.china_mainland,
    )


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
    print(f"\t Location: {api.iphone.location}\n")


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

    print(f"List of videos ({len(api.photos.albums['Videos'])}):")
    for idx, photo in enumerate(api.photos.albums["Videos"]):
        print(f"\t{idx}: {photo.filename} ({photo.item_type})")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)

    album = None
    print(f"List of shared albums ({len(api.photos.shared_streams)}):")
    for idx, album in enumerate(api.photos.shared_streams):
        print(f"\t{idx}: {album}")
        if idx >= MAX_DISPLAY - 1:
            break
    print(END_LIST)

    if album:
        print(
            f"List of shared photos [{album}] ({len(api.photos.shared_streams[album])}):"
        )
        for idx, photo in enumerate(api.photos.shared_streams[album]):
            print(f"\t{idx}: {photo.filename} ({photo.item_type})")

            if idx >= MAX_DISPLAY - 1:
                break
        print(END_LIST)


def main() -> None:
    """main function"""
    api: PyiCloudService = get_api()
    display_devices(api)
    display_calendars(api)
    display_contacts(api)
    display_drive(api)
    display_files(api)
    display_photos(api)


if __name__ == "__main__":
    main()
