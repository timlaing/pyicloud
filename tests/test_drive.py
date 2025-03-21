"""Drive service tests."""

from typing import Optional

import pytest

from pyicloud.base import PyiCloudService
from pyicloud.services.drive import DriveNode, DriveService


def test_root(pyicloud_service_working: PyiCloudService) -> None:
    """Test the root folder."""
    drive: DriveService = pyicloud_service_working.drive
    # root name is now extracted from drivewsid.
    assert drive.name == "root"
    assert drive.type == "folder"
    assert drive.size is None
    assert drive.date_changed is None
    assert drive.date_modified is None
    assert drive.date_last_open is None
    assert drive.dir() == ["Keynote", "Numbers", "Pages", "Preview", "pyiCloud"]


def test_trash(pyicloud_service_working: PyiCloudService) -> None:
    """Test the trash folder."""
    trash: DriveNode = pyicloud_service_working.drive.trash
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


def test_trash_recover(pyicloud_service_working: PyiCloudService) -> None:
    """Test recovering a file from the Trash."""
    trash_node = pyicloud_service_working.drive.trash["test_random_uuid"]
    assert trash_node is not None
    recover_result = trash_node.recover()
    recover_result_items = recover_result["items"][0]
    assert recover_result_items["status"] == "OK"
    assert recover_result_items["parentId"] == "FOLDER::com.apple.CloudDocs::root"
    assert recover_result_items["name"] == "test_random_uuid"


def test_trash_delete_forever(pyicloud_service_working: PyiCloudService) -> None:
    """Test permanently deleting a file from the Trash."""
    node = pyicloud_service_working.drive.trash["test_delete_forever_and_ever"]
    assert node is not None, "Expected a valid trash node before deleting forever."
    recover_result = node.delete_forever()
    recover_result_items = recover_result["items"][0]
    assert recover_result_items["status"] == "OK"
    assert (
        recover_result_items["parentId"]
        == "FOLDER::com.apple.CloudDocs::43D7C666-6E6E-4522-8999-0B519C3A1F4B"
    )
    assert recover_result_items["name"] == "test_delete_forever_and_ever"


def test_folder_app(pyicloud_service_working: PyiCloudService) -> None:
    """Test the /Preview folder."""
    folder: Optional[DriveNode] = pyicloud_service_working.drive["Preview"]
    assert folder
    assert folder.name == "Preview"
    assert folder.type == "app_library"
    assert folder.size is None
    assert folder.date_changed is None
    assert folder.date_modified is None
    assert folder.date_last_open is None
    with pytest.raises(KeyError, match="No items in folder, status: ID_INVALID"):
        folder.dir()


def test_folder_not_exists(pyicloud_service_working: PyiCloudService) -> None:
    """Test the /not_exists folder."""
    with pytest.raises(KeyError, match="No child named 'not_exists' exists"):
        pyicloud_service_working.drive["not_exists"]  # pylint: disable=pointless-statement


def test_folder(pyicloud_service_working: PyiCloudService) -> None:
    """Test the /pyiCloud folder."""
    folder: Optional[DriveNode] = pyicloud_service_working.drive["pyiCloud"]
    assert folder
    assert folder.name == "pyiCloud"
    assert folder.type == "folder"
    assert folder.size is None
    assert folder.date_changed is None
    assert folder.date_modified is None
    assert folder.date_last_open is None
    assert folder.dir() == ["Test"]


def test_subfolder(pyicloud_service_working: PyiCloudService) -> None:
    """Test the /pyiCloud/Test folder."""
    parent_folder: Optional[DriveNode] = pyicloud_service_working.drive["pyiCloud"]
    assert parent_folder is not None, "Expected to find 'pyiCloud' folder."
    folder: Optional[DriveNode] = parent_folder["Test"]
    assert folder
    assert folder.name == "Test"
    assert folder.type == "folder"
    assert folder.size is None
    assert folder.date_changed is None
    assert folder.date_modified is None
    assert folder.date_last_open is None
    assert folder.dir() == ["Document scanné 2.pdf", "Scanned document 1.pdf"]


def test_subfolder_file(pyicloud_service_working: PyiCloudService) -> None:
    """Test the /pyiCloud/Test/Scanned document 1.pdf file."""
    drive: Optional[DriveNode] = pyicloud_service_working.drive["pyiCloud"]
    assert drive
    folder: Optional[DriveNode] = drive["Test"]
    assert folder
    file_test: Optional[DriveNode] = folder["Scanned document 1.pdf"]
    assert file_test
    assert file_test.name == "Scanned document 1.pdf"
    assert file_test.type == "file"
    assert file_test.size == 21644358
    assert str(file_test.date_changed) == "2020-05-03 00:16:17"
    assert str(file_test.date_modified) == "2020-05-03 00:15:17"
    assert str(file_test.date_last_open) == "2020-05-03 00:24:25"
    with pytest.raises(NotADirectoryError):
        file_test.dir()


def test_file_open(pyicloud_service_working: PyiCloudService) -> None:
    """Test the /pyiCloud/Test/Scanned document 1.pdf file open."""
    drive: Optional[DriveNode] = pyicloud_service_working.drive["pyiCloud"]
    assert drive
    folder: Optional[DriveNode] = drive["Test"]
    assert folder
    file_test: Optional[DriveNode] = folder["Scanned document 1.pdf"]
    assert file_test
    with file_test.open(stream=True) as response:
        assert response.raw
