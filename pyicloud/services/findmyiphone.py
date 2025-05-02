"""Find my iPhone service."""

import json
from typing import Any, Iterator, Optional

from requests import Response

from pyicloud.exceptions import PyiCloudNoDevicesException
from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession


class FindMyiPhoneServiceManager(BaseService):
    """The 'Find my iPhone' iCloud service

    This connects to iCloud and return phone data including the near-realtime
    latitude and longitude.
    """

    def __init__(
        self,
        service_root: str,
        session: PyiCloudSession,
        params: dict[str, Any],
        with_family=False,
    ) -> None:
        super().__init__(service_root, session, params)
        self.with_family: bool = with_family

        fmip_endpoint: str = f"{service_root}/fmipservice/client/web"
        self._fmip_refresh_url: str = f"{fmip_endpoint}/refreshClient"
        self._fmip_sound_url: str = f"{fmip_endpoint}/playSound"
        self._fmip_message_url: str = f"{fmip_endpoint}/sendMessage"
        self._fmip_lost_url: str = f"{fmip_endpoint}/lostDevice"

        self._devices: dict[str, AppleDevice] = {}
        self.refresh_client()

    def refresh_client(self) -> None:
        """Refreshes the FindMyiPhoneService endpoint,

        This ensures that the location data is up-to-date.

        """
        req: Response = self.session.post(
            self._fmip_refresh_url,
            params=self.params,
            data=json.dumps(
                {
                    "clientContext": {
                        "appName": "iCloud Find (Web)",
                        "appVersion": "2.0",
                        "apiVersion": "3.0",
                        "deviceListVersion": 1,
                        "fmly": self.with_family,
                    }
                }
            ),
        )
        self.response: dict[str, Any] = req.json()

        for device_info in self.response["content"]:
            device_id: str = device_info["id"]
            if device_id not in self._devices:
                self._devices[device_id] = AppleDevice(
                    device_info,
                    self.params,
                    manager=self,
                    sound_url=self._fmip_sound_url,
                    lost_url=self._fmip_lost_url,
                    message_url=self._fmip_message_url,
                )
            else:
                self._devices[device_id].update(device_info)

        if not self._devices:
            raise PyiCloudNoDevicesException()

    def __getitem__(self, key) -> "AppleDevice":
        if isinstance(key, int):
            key = list(self.keys())[key]
        return self._devices[key]

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._devices, attr)

    def __str__(self) -> str:
        return f"{self._devices}"

    def __repr__(self) -> str:
        return f"{self}"

    def __iter__(self) -> Iterator["AppleDevice"]:
        return iter(self._devices.values())

    def __len__(self) -> int:
        return len(self._devices)


class AppleDevice:
    """Apple device."""

    def __init__(
        self,
        content: dict[str, Any],
        params: dict[str, Any],
        manager: FindMyiPhoneServiceManager,
        sound_url: str,
        lost_url: str,
        message_url: str,
    ) -> None:
        self.content: dict[str, Any] = content
        self.manager: FindMyiPhoneServiceManager = manager
        self.params: dict[str, Any] = params

        self.sound_url: str = sound_url
        self.lost_url: str = lost_url
        self.message_url: str = message_url

    @property
    def session(self) -> PyiCloudSession:
        """Gets the session."""
        return self.manager.session

    def update(self, data) -> None:
        """Updates the device data."""
        self.content = data

    @property
    def location(self):
        """Updates the device location."""
        self.manager.refresh_client()
        return self.content["location"]

    def status(self, additional: Optional[list[str]] = None) -> dict[str, Any]:
        """Returns status information for device.

        This returns only a subset of possible properties.
        """
        self.manager.refresh_client()
        fields: list[str] = [
            "batteryLevel",
            "deviceDisplayName",
            "deviceStatus",
            "name",
        ]
        if additional is not None:
            fields += additional

        properties: dict[str, Any] = {}
        for field in fields:
            properties[field] = self.content.get(field)
        return properties

    def play_sound(self, subject="Find My iPhone Alert") -> None:
        """Send a request to the device to play a sound.

        It's possible to pass a custom message by changing the `subject`.
        """
        data: str = json.dumps(
            {
                "device": self.content["id"],
                "subject": subject,
                "clientContext": {"fmly": True},
            }
        )
        self.session.post(self.sound_url, params=self.params, data=data)

    def display_message(
        self, subject="Find My iPhone Alert", message="This is a note", sounds=False
    ) -> None:
        """Send a request to the device to play a sound.

        It's possible to pass a custom message by changing the `subject`.
        """
        data: str = json.dumps(
            {
                "device": self.content["id"],
                "subject": subject,
                "sound": sounds,
                "userText": True,
                "text": message,
            }
        )
        self.session.post(self.message_url, params=self.params, data=data)

    def lost_device(
        self, number, text="This iPhone has been lost. Please call me.", newpasscode=""
    ) -> None:
        """Send a request to the device to trigger 'lost mode'.

        The device will show the message in `text`, and if a number has
        been passed, then the person holding the device can call
        the number without entering the passcode.
        """
        data: str = json.dumps(
            {
                "text": text,
                "userText": True,
                "ownerNbr": number,
                "lostModeEnabled": True,
                "trackingEnabled": True,
                "device": self.content["id"],
                "passcode": newpasscode,
            }
        )
        self.session.post(self.lost_url, params=self.params, data=data)

    @property
    def data(self) -> dict[str, Any]:
        """Gets the device data."""
        return self.content

    def __getitem__(self, key) -> Any:
        return self.content[key]

    def __getattr__(self, attr) -> Any:
        if attr in self.content:
            return self.content[attr]
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{attr}'"
        )

    def __str__(self) -> str:
        return f"{self['deviceDisplayName']}: {self['name']}"

    def __repr__(self) -> str:
        return f"<AppleDevice({self})>"
