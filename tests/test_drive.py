"""Drive service tests."""

from unittest import TestCase
from unittest.mock import mock_open, patch

import pytest

from . import PyiCloudServiceMock
from .const import AUTHENTICATED_USER, VALID_PASSWORD
from .const_login import LOGIN_WORKING


class DriveServiceTest(TestCase):
    """Drive service tests."""

    def setUp(self) -> None:
        """Set up tests."""
        self.apple_id = "test@example.com"
        self.password = "password"
        self.service = self.create_service_with_mock_authenticate()

    @patch("builtins.open", mock_open)
    def create_service_with_mock_authenticate(self):
        with patch("pyicloud.base.PyiCloudService.authenticate") as mock_authenticate:
            # Mock the authenticate method during initialization
            mock_authenticate.return_value = None

            service = PyiCloudServiceMock(self.apple_id, self.password)
            service.data = LOGIN_WORKING
            service._webservices = service.data["webservices"]

        return service

    def test_root(self):
        """Test the root folder."""
        drive = self.service.drive
        # root name is now extracted from drivewsid.
        assert drive.name == "root"
        assert drive.type == "folder"
        assert drive.size is None
        assert drive.date_changed is None
        assert drive.date_modified is None
        assert drive.date_last_open is None
        assert drive.dir() == ["Keynote", "Numbers", "Pages", "Preview", "pyiCloud"]

    def test_trash(self):
        """Test the root folder."""
        trash = self.service.drive.trash
        assert trash.name == "TRASH_ROOT"
        assert trash.type == "trash"
        assert trash.size is None
        assert trash.date_changed is None
        assert trash.date_modified is None
        assert trash.date_last_open is None
        assert trash.dir() == [
            "dead-file.download",
            "test_create_folder",
            "test_delete_forever_and_ever",
            "test_files_1",
            "test_random_uuid",
            "test12345",
        ]

    def test_trash_recover(self):
        """Test recovering a file from the Trash."""
        recover_result = self.service.drive.trash["test_random_uuid"].recover()  # type: ignore
        recover_result_items = recover_result["items"][0]
        assert recover_result_items["status"] == "OK"
        assert recover_result_items["parentId"] == "FOLDER::com.apple.CloudDocs::root"
        assert recover_result_items["name"] == "test_random_uuid"

    def test_trash_delete_forever(self):
        """Test permanently deleting a file from the Trash."""
        recover_result = self.service.drive.trash[
            "test_delete_forever_and_ever"
        ].delete_forever()  # type: ignore
        recover_result_items = recover_result["items"][0]
        assert recover_result_items["status"] == "OK"
        assert (
            recover_result_items["parentId"]
            == "FOLDER::com.apple.CloudDocs::43D7C666-6E6E-4522-8999-0B519C3A1F4B"
        )
        assert recover_result_items["name"] == "test_delete_forever_and_ever"

    def test_folder_app(self):
        """Test the /Preview folder."""
        folder = self.service.drive["Preview"]
        assert folder
        assert folder.name == "Preview"
        assert folder.type == "app_library"
        assert folder.size is None
        assert folder.date_changed is None
        assert folder.date_modified is None
        assert folder.date_last_open is None
        with pytest.raises(KeyError, match="No items in folder, status: ID_INVALID"):
            assert folder.dir()

    def test_folder_not_exists(self):
        """Test the /not_exists folder."""
        with pytest.raises(KeyError, match="No child named 'not_exists' exists"):
            self.service.drive["not_exists"]  # pylint: disable=pointless-statement

    def test_folder(self):
        """Test the /pyiCloud folder."""
        folder = self.service.drive["pyiCloud"]
        assert folder
        assert folder.name == "pyiCloud"
        assert folder.type == "folder"
        assert folder.size is None
        assert folder.date_changed is None
        assert folder.date_modified is None
        assert folder.date_last_open is None
        assert folder.dir() == ["Test"]

    def test_subfolder(self):
        """Test the /pyiCloud/Test folder."""
        folder = self.service.drive["pyiCloud"]["Test"]  # type: ignore
        assert folder
        assert folder.name == "Test"
        assert folder.type == "folder"
        assert folder.size is None
        assert folder.date_changed is None
        assert folder.date_modified is None
        assert folder.date_last_open is None
        assert folder.dir() == ["Document scanné 2.pdf", "Scanned document 1.pdf"]

    def test_subfolder_file(self):
        """Test the /pyiCloud/Test/Scanned document 1.pdf file."""
        folder = self.service.drive["pyiCloud"]["Test"]  # type: ignore
        assert folder
        file_test = folder["Scanned document 1.pdf"]
        assert file_test
        assert file_test.name == "Scanned document 1.pdf"
        assert file_test.type == "file"
        assert file_test.size == 21644358
        assert str(file_test.date_changed) == "2020-05-03 00:16:17"
        assert str(file_test.date_modified) == "2020-05-03 00:15:17"
        assert str(file_test.date_last_open) == "2020-05-03 00:24:25"
        assert file_test.dir() is None

    @patch("builtins.open", mock_open)
    def test_file_open(self):
        """Test the /pyiCloud/Test/Scanned document 1.pdf file open."""
        file_test = self.service.drive["pyiCloud"]["Test"]["Scanned document 1.pdf"]  # type: ignore
        assert file_test
        with file_test.open(stream=True) as response:
            assert response.raw
