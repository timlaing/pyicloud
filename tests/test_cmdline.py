"""Cmdline tests."""

import pickle
import re
from io import BytesIO
from typing import Callable
from unittest import TestCase
from unittest.mock import mock_open, patch

import pytest

from pyicloud import cmdline

from . import PyiCloudServiceMock
from .const import AUTHENTICATED_USER, REQUIRES_2FA_USER, VALID_2FA_CODE, VALID_PASSWORD
from .const_findmyiphone import FMI_FAMILY_WORKING


class TestCmdline(TestCase):
    """Cmdline test cases."""

    def setUp(self):
        """Set up tests."""
        cmdline.PyiCloudService = PyiCloudServiceMock
        self.main: Callable = cmdline.main

    def test_no_arg(self):
        """Test no args."""
        with pytest.raises(SystemExit, match="2"):
            self.main()

        with pytest.raises(SystemExit, match="2"):
            self.main(None)

        with pytest.raises(SystemExit, match="2"):
            self.main([])

    def test_help(self):
        """Test the help command."""
        with pytest.raises(SystemExit, match="0"):
            self.main(
                [
                    "--help",
                ]
            )

    def test_username(self):
        """Test the username command."""
        # No username supplied
        with pytest.raises(SystemExit, match="2"):
            self.main(
                [
                    "--username",
                ]
            )

    @patch("builtins.open", new_callable=mock_open)
    @patch("keyring.get_password", return_value=None)
    @patch("getpass.getpass")
    def test_username_password_invalid(
        self, mock_getpass, mock_get_password, mock_open
    ):  # pylint: disable=unused-argument
        """Test username and password commands."""
        # No password supplied
        mock_getpass.return_value = None
        with pytest.raises(SystemExit, match="2"):
            self.main(
                [
                    "--username",
                    "invalid_user",
                ]
            )

        # Bad username or password
        mock_getpass.return_value = "invalid_pass"
        with pytest.raises(
            RuntimeError, match="Bad username or password for invalid_user"
        ):
            self.main(
                [
                    "--username",
                    "invalid_user",
                ]
            )

        # We should not use getpass for this one, but we reset the password at login fail
        with pytest.raises(
            RuntimeError, match="Bad username or password for invalid_user"
        ):
            self.main(
                [
                    "--username",
                    "invalid_user",
                    "--password",
                    "invalid_pass",
                ]
            )

    @patch("builtins.open", new_callable=mock_open)
    @patch("keyring.get_password", return_value=None)
    @patch("pyicloud.cmdline.input")
    def test_username_password_requires_2fa(
        self, mock_input, mock_get_password, mock_open
    ):  # pylint: disable=unused-argument
        """Test username and password commands."""
        # Valid connection for the first time
        mock_input.return_value = VALID_2FA_CODE
        with self.assertRaises(SystemExit):
            self.main(
                [
                    "--username",
                    REQUIRES_2FA_USER,
                    "--password",
                    VALID_PASSWORD,
                    "--non-interactive",
                ]
            )

    @patch("keyring.get_password", return_value=None)
    def test_device_outputfile(
        self, mock_get_password
    ):  # pylint: disable=unused-argument
        """Test the outputfile command."""

        # Dictionary to store written data
        written_data = {}

        # Custom side effect function for open
        def mock_file_open(filepath, mode="r", *args, **kwargs):
            if "w" in mode or "a" in mode:
                # Writing or appending mode
                def mock_write(content):
                    if filepath not in written_data:
                        written_data[filepath] = ""
                    if "a" in mode:  # Append mode
                        written_data[filepath] += content
                    else:  # Write mode
                        written_data[filepath] = content

                mock_file = mock_open().return_value
                mock_file.write = mock_write
                return mock_file
            elif "r" in mode:
                raise FileNotFoundError(f"No such file or directory: '{filepath}'")
            else:
                raise ValueError(f"Unsupported mode: {mode}")

        with patch("builtins.open", mock_file_open) as mocked_open:
            with self.assertRaises(SystemExit):
                self.main(
                    [
                        "--username",
                        AUTHENTICATED_USER,
                        "--password",
                        VALID_PASSWORD,
                        "--non-interactive",
                        "--outputfile",
                    ]
                )

            devices = FMI_FAMILY_WORKING.get("content")
            if devices:
                for device in devices:
                    file_name = device.get("name").strip().lower() + ".fmip_snapshot"
                    self.assertIn(file_name, written_data)
                    buffer = BytesIO(written_data[file_name])

                    contents = []
                    while True:
                        try:
                            contents.append(pickle.load(buffer))
                        except EOFError:
                            break
                    assert contents == [device]
