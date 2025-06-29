"""Find my iPhone service."""

from typing import Any, Iterator, Optional

from requests import Response

from pyicloud.exceptions import PyiCloudNoDevicesException, PyiCloudServiceUnavailable
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
        token_endpoint: str,
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
        self._fmip_erase_url: str = f"{fmip_endpoint}/remoteWipeWithUserAuth"
        self._erase_token_url: str = f"{token_endpoint}/fmipWebAuthenticate"

        self._devices: dict[str, AppleDevice] = {}
        self.refresh_client()

    def refresh_client(self) -> None:
        """Refreshes the FindMyiPhoneService endpoint,

        This ensures that the location data is up-to-date.

        """
        req: Response = self.session.post(
            self._fmip_refresh_url,
            params=self.params,
            json={
                "clientContext": {
                    "appName": "iCloud Find (Web)",
                    "appVersion": "2.0",
                    "apiVersion": "3.0",
                    "deviceListVersion": 1,
                    "fmly": self.with_family,
                }
            },
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
                    erase_url=self._fmip_erase_url,
                    erase_token_url=self._erase_token_url,
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
        erase_url: str,
        erase_token_url: str,
        message_url: str,
    ) -> None:
        self._content: dict[str, Any] = content
        self._manager: FindMyiPhoneServiceManager = manager
        self._params: dict[str, Any] = params

        self._sound_url: str = sound_url
        self._lost_url: str = lost_url
        self._erase_url: str = erase_url
        self._erase_token_url: str = erase_token_url
        self._message_url: str = message_url

    @property
    def session(self) -> PyiCloudSession:
        """Gets the session."""
        return self._manager.session

    def update(self, data) -> None:
        """Updates the device data."""
        self._content = data

    @property
    def location(self) -> Optional[dict[str, Any]]:
        """Updates the device location."""
        self._manager.refresh_client()
        return self._content["location"]

    def status(self, additional: Optional[list[str]] = None) -> dict[str, Any]:
        """Returns status information for device.

        This returns only a subset of possible properties.
        """
        self._manager.refresh_client()
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
            properties[field] = self._content.get(field)
        return properties

    def play_sound(self, subject="Find My iPhone Alert") -> None:
        """Send a request to the device to play a sound.

        It's possible to pass a custom message by changing the `subject`.
        """
        data: dict[str, Any] = {
            "device": self._content["id"],
            "subject": subject,
            "clientContext": {"fmly": True},
        }

        self.session.post(self._sound_url, params=self._params, json=data)

    def display_message(
        self, subject="Find My iPhone Alert", message="This is a note", sounds=False
    ) -> None:
        """Send a request to the device to play a sound.

        It's possible to pass a custom message by changing the `subject`.
        """
        data: dict[str, Any] = {
            "device": self._content["id"],
            "subject": subject,
            "sound": sounds,
            "userText": True,
            "text": message,
        }

        self.session.post(self._message_url, params=self._params, json=data)

    def lost_device(
        self,
        number: str,
        text: str = "This device has been lost. Please call me.",
        newpasscode: str = "",
    ) -> None:
        """Send a request to the device to trigger 'lost mode'.

        The device will show the message in `text`, and if a number has
        been passed, then the person holding the device can call
        the number without entering the passcode.
        """
        data: dict[str, Any] = {
            "text": text,
            "userText": True,
            "ownerNbr": number,
            "lostModeEnabled": True,
            "trackingEnabled": True,
            "device": self._content["id"],
            "passcode": newpasscode,
        }

        self.session.post(self._lost_url, params=self._params, json=data)

    def _get_erase_token(self) -> str:
        """Get the erase token for the Find My iPhone service."""
        data: dict[str, Any] = {
            "dsWebAuthToken": self.session.data.get("session_token"),
        }

        data = self.session.post(url=self._erase_token_url, json=data).json()
        if "tokens" not in data or "mmeFMIPWebEraseDeviceToken" not in data["tokens"]:
            raise PyiCloudServiceUnavailable("Find My iPhone erase token not available")
        return data["tokens"]["mmeFMIPWebEraseDeviceToken"]

    def erase_device(
        self,
        text: str = "This device has been lost. Please call me.",
        newpasscode: str = "",
    ) -> None:
        """Send a request to the device to start a remote erase."""
        data: dict[str, Any] = {
            "authToken": self._get_erase_token(),
            "text": text,
            "device": self._content["id"],
            "passcode": newpasscode,
        }

        self.session.post(self._erase_url, params=self._params, json=data)

    @property
    def data(self) -> dict[str, Any]:
        """Gets the device data."""
        return self._content

    def __getitem__(self, key) -> Any:
        return self._content[key]

    def __getattr__(self, attr) -> Any:
        if attr in self._content:
            return self._content[attr]
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{attr}'"
        )

    def __str__(self) -> str:
        return f"{self['deviceDisplayName']}: {self['name']}"

    def __repr__(self) -> str:
        return f"<AppleDevice({self})>"
