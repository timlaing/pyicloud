"""Services."""

from pyicloud.services.account import AccountService
from pyicloud.services.calendar import CalendarService
from pyicloud.services.contacts import ContactsService
from pyicloud.services.drive import DriveService
from pyicloud.services.findmyiphone import AppleDevice, FindMyiPhoneServiceManager
from pyicloud.services.hidemyemail import HideMyEmailService
from pyicloud.services.photos import PhotosService
from pyicloud.services.reminders import RemindersService
from pyicloud.services.ubiquity import UbiquityService

__all__: list[str] = [
    "AppleDevice",
    "AccountService",
    "CalendarService",
    "ContactsService",
    "DriveService",
    "FindMyiPhoneServiceManager",
    "HideMyEmailService",
    "PhotosService",
    "RemindersService",
    "UbiquityService",
]
