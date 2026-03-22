import importlib.util
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "example_reminders_delta.py",
)


def _load_example_reminders_delta():
    spec = importlib.util.spec_from_file_location(
        "pyicloud_example_reminders_delta",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestExampleRemindersDelta(unittest.TestCase):
    def test_authenticate_uses_security_key_when_fido2_devices_are_available(self):
        module = _load_example_reminders_delta()
        api = MagicMock()
        devices = [object(), object()]
        api.requires_2fa = True
        api.requires_2sa = False
        api.fido2_devices = devices
        api.is_trusted_session = False

        with (
            patch.object(module, "resolve_credentials", return_value=("u", "p")),
            patch.object(module, "PyiCloudService", return_value=api),
            patch("builtins.input", return_value="1"),
        ):
            result = module.authenticate(SimpleNamespace())

        self.assertIs(result, api)
        api.confirm_security_key.assert_called_once_with(devices[1])
        api.validate_2fa_code.assert_not_called()
        api.trust_session.assert_called_once_with()

    def test_authenticate_2sa_uses_selected_trusted_device(self):
        module = _load_example_reminders_delta()
        api = MagicMock()
        devices = [
            {"id": "device-0"},
            {"id": "device-1"},
        ]
        api.requires_2fa = False
        api.requires_2sa = True
        api.trusted_devices = devices

        with (
            patch.object(module, "resolve_credentials", return_value=("u", "p")),
            patch.object(module, "PyiCloudService", return_value=api),
            patch("builtins.input", side_effect=["1", "123456"]),
        ):
            result = module.authenticate(SimpleNamespace())

        self.assertIs(result, api)
        api.send_verification_code.assert_called_once_with(devices[1])
        api.validate_verification_code.assert_called_once_with(devices[1], "123456")
