"""Find my iPhone service."""

import logging
import threading
import time
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Callable, Iterator, Optional

from requests import Response

from pyicloud.exceptions import (
    PyiCloudAuthRequiredException,
    PyiCloudNoDevicesException,
    PyiCloudServiceUnavailable,
)
from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession

_FMIP_CLIENT_CONTEXT_TIMEZONE: str = "US/Pacific"
_LOGGER: logging.Logger = logging.getLogger(__name__)
_MAX_REFRESH_RETRIES: int = 5


def _monitor_thread(
    interval: float, func: Callable, stop_event: threading.Event, locate: bool = True
) -> None:
    next_event: datetime = datetime.now() + timedelta(seconds=interval)
    while not stop_event.wait(timeout=interval):
        if next_event < datetime.now():
            try:
                func(locate)
            except Exception as exc:
                _LOGGER.debug("FindMyiPhone monitor thread error: %s", exc)
            next_event = datetime.now() + timedelta(seconds=interval)


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
        """Initialize the FindMyiPhoneServiceManager."""
        super().__init__(service_root, session, params)
        self._with_family: bool = with_family

        fmip_endpoint: str = f"{service_root}/fmipservice/client/web"
        self._fmip_init_url: str = f"{fmip_endpoint}/initClient"
        self._fmip_refresh_url: str = f"{fmip_endpoint}/refreshClient"
        self._fmip_sound_url: str = f"{fmip_endpoint}/playSound"
        self._fmip_message_url: str = f"{fmip_endpoint}/sendMessage"
        self._fmip_lost_url: str = f"{fmip_endpoint}/lostDevice"
        self._fmip_erase_url: str = f"{fmip_endpoint}/remoteWipeWithUserAuth"
        self._erase_token_url: str = f"{token_endpoint}/fmipWebAuthenticate"

        self._devices: dict[str, AppleDevice] = {}
        self._devices_names: list[str] = []
        self._server_ctx: dict[str, Any] | None = None
        self._user_info: dict[str, Any] | None = None
        self._monitor: Optional[threading.Thread] = None
        self.stop_event: threading.Event = threading.Event()

        self._refresh_client_with_reauth(locate=True)

    def _refresh_client_with_reauth(
        self, retry: bool = False, locate: bool = True
    ) -> None:
        """
        Refreshes the FindMyiPhoneService endpoint with re-authentication.
        This ensures that the location data is up-to-date.
        """
        # Refresh the client (own devices first)
        self.stop_event.set()
        try:
            self._refresh_client(locate=locate)
        except PyiCloudAuthRequiredException:
            if retry is True:
                raise

            _LOGGER.debug("Re-authenticating session")
            self._server_ctx = None
            self.session.service.authenticate(force_refresh=True)
            self._refresh_client_with_reauth(retry=True, locate=locate)
            return

        # Initialize devices (including family devices if enabled)
        self._initialize_devices(locate=locate)
        self._start_monitor_thread()

    def _initialize_devices(self, locate: bool = False) -> None:
        """Initializes the devices for the FindMyiPhoneServiceManager."""

        # If family sharing is enabled, we may need to poll until all devices are ready
        # This is indicated by the deviceFetchStatus being "LOADING"
        retries: int = 0
        while (
            self._with_family
            and self._user_info
            and self._user_info.get("hasMembers", False)
        ):
            needs_refresh: bool = False
            for user in self._user_info.get("membersInfo", {}).values():
                if user.get("deviceFetchStatus") == "LOADING":
                    needs_refresh = True
                    break

            if needs_refresh:
                time.sleep(0.1)
                self._refresh_client(locate=locate)
                retries += 1
                if retries >= _MAX_REFRESH_RETRIES:
                    _LOGGER.debug("Max retries reached when fetching family devices")
                    break
            else:
                break

        if not self._devices:
            raise PyiCloudNoDevicesException()

        _LOGGER.info("Number of devices found: %d", len(self._devices))

    def _start_monitor_thread(self) -> None:
        """Starts the monitor thread for the FindMyiPhoneServiceManager."""
        if not self.is_alive:
            self._monitor = threading.Thread(
                target=_monitor_thread,
                kwargs={
                    "func": self._refresh_client,
                    "interval": 1.0,
                    "stop_event": self.stop_event,
                },
                daemon=True,
            )
            self.stop_event.clear()
            self._monitor.start()

    def _refresh_client(self, locate: bool = False) -> None:
        """
        Refreshes the FindMyiPhoneService endpoint, this ensures that the location data
        is up-to-date.
        """
        req_json: dict[str, Any] = {
            "clientContext": {
                "appName": "iCloud Find (Web)",
                "appVersion": "2.0",
                "apiVersion": "3.0",
                "deviceListVersion": 1,
                "fmly": self._with_family,
                "timezone": _FMIP_CLIENT_CONTEXT_TIMEZONE,
                "inactiveTime": 0,
            },
        }

        if self._server_ctx:
            req_json["serverContext"] = self._server_ctx
            if locate:
                req_json["isUpdatingAllLocations"] = True
                req_json["clientContext"].update(
                    {
                        "shouldLocate": True,
                        "selectedDevice": "all",
                    }
                )

        req: Response = self.session.post(
            url=self._fmip_refresh_url if self._server_ctx else self._fmip_init_url,
            params=self.params,
            json=req_json,
        )
        resp: dict[str, Any] = req.json()

        self._server_ctx = resp.get("serverContext")
        if self._server_ctx and "theftLoss" in self._server_ctx:
            self._server_ctx["theftLoss"] = None

        self._user_info = resp.get("userInfo")

        if "content" not in resp:
            _LOGGER.debug("FMIP returned 0 devices")
            return

        _LOGGER.debug("FMIP returned %d devices", len(resp["content"]))
        for device_info in resp["content"]:
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

        self._devices_names = list(self._devices.keys())

    def refresh(self, locate: bool = False) -> None:
        """Public method to refresh the FindMyiPhoneService endpoint."""
        self._refresh_client_with_reauth(locate=locate)

    def __getitem__(self, key: str | int) -> "AppleDevice":
        """Gets a device by name or index."""
        if not self.is_alive:
            self._refresh_client_with_reauth(locate=True)

        if isinstance(key, int):
            key = self._devices_names[key]
        return self._devices[key]

    def __str__(self) -> str:
        """String representation of the devices."""
        return f"{self._devices}"

    def __repr__(self) -> str:
        """Representation of the device."""
        return f"{self}"

    def __iter__(self) -> Iterator["AppleDevice"]:
        """Iterates over the devices."""

        if not self.is_alive:
            self._refresh_client_with_reauth(locate=True)
        return iter(self._devices.values())

    def __len__(self) -> int:
        """Returns the number of devices."""
        if not self.is_alive:
            self._refresh_client_with_reauth(locate=True)
        return len(self._devices)

    @property
    def is_alive(self) -> bool:
        """Indicates if the service is alive."""
        return self._monitor is not None and self._monitor.is_alive()

    @property
    def devices(self) -> "MappingProxyType[str, AppleDevice]":
        """Returns the devices."""
        return MappingProxyType(self._devices)

    @property
    def user_info(self) -> Optional[MappingProxyType[str, Any]]:
        """Returns the user info."""
        return MappingProxyType(self._user_info) if self._user_info else None


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
        """Initialize the Apple device."""
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
        if self.location_available is False:
            return None
        return self._content["location"]

    def status(self, additional: Optional[list[str]] = None) -> dict[str, Any]:
        """Returns status information for device.

        This returns only a subset of possible properties.
        """
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
        if self.sound_available is False:
            raise PyiCloudServiceUnavailable("Sound is not available for this device")
        data: dict[str, Any] = {
            "device": self._content["id"],
            "subject": subject,
            "clientContext": {"fmly": True},
        }

        self.session.post(self._sound_url, params=self._params, json=data)

    def display_message(
        self,
        subject="Find My iPhone Alert",
        message="This is a note",
        sounds=False,
        vibrate=False,
        strobe=False,
    ) -> None:
        """Send a request to the device to a display a message.

        It's possible to pass a custom message by changing the `subject`.
        """
        if self.messaging_available is False:
            raise PyiCloudServiceUnavailable(
                "Messaging is not available for this device"
            )

        data: dict[str, Any] = {
            "device": self._content["id"],
            "subject": subject,
            "userText": True,
            "text": message,
            "sound": sounds,
            "vibrate": vibrate,
            "strobe": strobe,
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
        if self.lost_mode_available is False:
            raise PyiCloudServiceUnavailable(
                "Lost mode is not available for this device"
            )

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
        if self.erase_available is False:
            raise PyiCloudServiceUnavailable("Erase is not available for this device")

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
        if not self._manager.is_alive:
            self._manager.refresh()

        return self._content

    def __getitem__(self, key) -> Any:
        """Gets an attribute of the device data."""
        if not self._manager.is_alive:
            self._manager.refresh()

        return self._content[key]

    def __getattr__(self, attr) -> Any:
        """Gets an attribute of the device data."""
        if not self._manager.is_alive:
            self._manager.refresh()

        if attr in self._content:
            return self._content[attr]
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{attr}'"
        )

    @property
    def name(self) -> str:
        """Gets the device name."""
        return self.data.get("name", "")

    @property
    def model(self) -> str:
        """Gets the device model."""
        return self.data.get("deviceModel", "")

    @property
    def model_name(self) -> str:
        """Gets the device model name."""
        return self.data.get("deviceDisplayName", "")

    @property
    def device_type(self) -> str:
        """Gets the device type."""
        return self.data.get("deviceClass", "")

    @property
    def lost_mode_available(self) -> bool:
        """Indicates if lost mode is available for the device."""
        return self.data.get("lostModeCapable", False)

    @property
    def messaging_available(self) -> bool:
        """Indicates if messaging is available for the device."""
        return self.data.get("features", {}).get("MSG", False)

    @property
    def sound_available(self) -> bool:
        """Indicates if sound is available for the device."""
        return self.data.get("features", {}).get("SND", False)

    @property
    def erase_available(self) -> bool:
        """Indicates if erase is available for the device."""
        return self.data.get("features", {}).get("WIP", False)

    @property
    def location_available(self) -> bool:
        """Indicates if location is available for the device."""
        return (
            self.data.get("features", {}).get("LOC", False)
            and "location" in self._content
            and self._content["location"] is not None
        )

    def __str__(self) -> str:
        """String representation of the device."""
        return f"{self.model_name}: {self.name}"

    def __repr__(self) -> str:
        """Representation of the device."""
        return f"<AppleDevice({self})>"
