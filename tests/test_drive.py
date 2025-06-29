"""Drive service tests."""
# pylint: disable=protected-access

from typing import Optional
from unittest.mock import ANY, Mock, patch

import pytest

from pyicloud.base import PyiCloudService
from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import PyiCloudAPIResponseException
from pyicloud.services.drive import (
    CLOUD_DOCS_ZONE,
    NODE_TRASH,
    DriveNode,
    DriveService,
)


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
    assert trash.name == NODE_TRASH
    assert trash.type == DriveNode.TYPE_TRASH
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
        _ = pyicloud_service_working.drive["not_exists"]


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


def test_get_node_data(pyicloud_service_working: PyiCloudService) -> None:
    """Test retrieving node data."""
    drive: DriveService = pyicloud_service_working.drive
    mock_response = {"drivewsid": "test_id", "name": "Test Node"}
    with patch.object(
        drive.session, "post", return_value=Mock(ok=True, json=lambda: [mock_response])
    ) as mock_post:
        node_data = drive.get_node_data("test_id")
        assert node_data == mock_response
        mock_post.assert_called_once_with(
            drive.service_root + "/retrieveItemDetailsInFolders",
            params=drive.params,
            json=[{"drivewsid": "test_id", "partialData": False}],
        )


def test_get_file(pyicloud_service_working: PyiCloudService) -> None:
    """Test retrieving a file."""
    drive: DriveService = pyicloud_service_working.drive
    mock_response = {"data_token": {"url": "https://example.com/file"}}
    with patch.object(
        drive.session,
        "get",
        side_effect=[
            Mock(ok=True, json=lambda: mock_response),
            Mock(ok=True, content=b"file content"),
        ],
    ) as mock_get:
        file_response = drive.get_file("file_id")
        assert file_response.content == b"file content"
        mock_get.assert_any_call(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/download/by_id",
            params={**drive.params, "document_id": "file_id"},
        )
        mock_get.assert_any_call("https://example.com/file", params=drive.params)


def test_create_folders(pyicloud_service_working: PyiCloudService) -> None:
    """Test creating a folder."""
    drive: DriveService = pyicloud_service_working.drive
    mock_response = {"folders": [{"name": "New Folder"}]}
    with patch.object(
        drive.session, "post", return_value=Mock(ok=True, json=lambda: mock_response)
    ) as mock_post:
        response = drive.create_folders("parent_id", "New Folder")
        assert response == mock_response
        mock_post.assert_called_once_with(
            drive.service_root + "/createFolders",
            params=drive.params,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json=ANY,
        )


def test_delete_items(pyicloud_service_working: PyiCloudService) -> None:
    """Test deleting an item."""
    drive: DriveService = pyicloud_service_working.drive
    mock_response = {"status": "OK"}
    with patch.object(
        drive.session, "post", return_value=Mock(ok=True, json=lambda: mock_response)
    ) as mock_post:
        response = drive.delete_items("node_id", "etag")
        assert response == mock_response
        mock_post.assert_called_once_with(
            drive.service_root + "/deleteItems",
            params=drive.params,
            json={
                "items": [
                    {
                        "drivewsid": "node_id",
                        "etag": "etag",
                        "clientId": drive.params["clientId"],
                    },
                ]
            },
        )


def test_rename_items(pyicloud_service_working: PyiCloudService) -> None:
    """Test renaming an item."""
    drive: DriveService = pyicloud_service_working.drive
    mock_response = {"status": "OK"}
    with patch.object(
        drive.session,
        "post",
        return_value=Mock(ok=True, json=lambda: mock_response),
    ) as mock_post:
        response = drive.rename_items("node_id", "etag", "New Name")
        assert response == mock_response
        mock_post.assert_called_once_with(
            drive.service_root + "/renameItems",
            params=drive.params,
            json={
                "items": [
                    {"drivewsid": "node_id", "etag": "etag", "name": "New Name"},
                ]
            },
        )


def test_move_items_to_trash(pyicloud_service_working: PyiCloudService) -> None:
    """Test moving an item to trash."""
    drive: DriveService = pyicloud_service_working.drive
    mock_response = {"status": "OK"}
    with patch.object(
        drive.session, "post", return_value=Mock(ok=True, json=lambda: mock_response)
    ) as mock_post:
        response = drive.move_items_to_trash("node_id", "etag")
        assert response == mock_response
        mock_post.assert_called_once_with(
            drive.service_root + "/moveItemsToTrash",
            params=drive.params,
            json={
                "items": [
                    {"drivewsid": "node_id", "etag": "etag", "clientId": "node_id"},
                ]
            },
        )


def test_recover_items_from_trash(pyicloud_service_working: PyiCloudService) -> None:
    """Test recovering an item from trash."""
    drive: DriveService = pyicloud_service_working.drive
    mock_response = {"status": "OK"}
    with patch.object(
        drive.session, "post", return_value=Mock(ok=True, json=lambda: mock_response)
    ) as mock_post:
        response = drive.recover_items_from_trash("node_id", "etag")
        assert response == mock_response
        mock_post.assert_called_once_with(
            drive.service_root + "/putBackItemsFromTrash",
            params=drive.params,
            json={
                "items": [
                    {"drivewsid": "node_id", "etag": "etag"},
                ]
            },
        )


def test_delete_forever_from_trash(pyicloud_service_working: PyiCloudService) -> None:
    """Test permanently deleting an item from trash."""
    drive: DriveService = pyicloud_service_working.drive
    mock_response = {"status": "OK"}
    with patch.object(
        drive.session, "post", return_value=Mock(ok=True, json=lambda: mock_response)
    ) as mock_post:
        response = drive.delete_forever_from_trash("node_id", "etag")
        assert response == mock_response
        mock_post.assert_called_once_with(
            drive.service_root + "/deleteItems",
            params=drive.params,
            json={
                "items": [
                    {"drivewsid": "node_id", "etag": "etag"},
                ]
            },
        )


def test_get_upload_contentws_url_success(
    mock_service_with_cookies: PyiCloudService,
) -> None:
    """Test successful retrieval of upload contentWS URL."""
    drive: DriveService = mock_service_with_cookies.drive
    mock_file = Mock()
    mock_file.name = "test_file.txt"
    mock_file.tell = Mock(side_effect=[0, 100, 0])  # Mock file size as 100 bytes

    mock_response = [
        {"document_id": "mock_document_id", "url": "https://example.com/upload"}
    ]
    with (
        patch.object(
            drive.session,
            "post",
            return_value=Mock(ok=True, json=lambda: mock_response),
        ) as mock_post,
        patch("mimetypes.guess_type", return_value=("text/plain", None)),
    ):
        document_id, url = drive._get_upload_contentws_url(mock_file)

        assert document_id == "mock_document_id"
        assert url == "https://example.com/upload"

        mock_post.assert_called_once_with(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/upload/web",
            params=ANY,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json={
                "filename": "test_file.txt",
                "type": "FILE",
                "content_type": "text/plain",
                "size": 100,
            },
        )


def test_get_upload_contentws_url_no_content_type(
    mock_service_with_cookies: PyiCloudService,
) -> None:
    """Test retrieval of upload contentWS URL when content type is None."""
    drive: DriveService = mock_service_with_cookies.drive
    mock_file = Mock()
    mock_file.name = "test_file.unknown"
    mock_file.tell = Mock(side_effect=[0, 200, 0])  # Mock file size as 200 bytes

    mock_response = [
        {"document_id": "mock_document_id", "url": "https://example.com/upload"}
    ]
    with (
        patch.object(
            drive.session,
            "post",
            return_value=Mock(ok=True, json=lambda: mock_response),
        ) as mock_post,
        patch("mimetypes.guess_type", return_value=(None, None)),
    ):
        document_id, url = drive._get_upload_contentws_url(mock_file)

        assert document_id == "mock_document_id"
        assert url == "https://example.com/upload"

        mock_post.assert_called_once_with(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/upload/web",
            params=ANY,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json={
                "filename": "test_file.unknown",
                "type": "FILE",
                "content_type": "",
                "size": 200,
            },
        )


def test_get_upload_contentws_url_error_response(
    mock_service_with_cookies: PyiCloudService,
) -> None:
    """Test retrieval of upload contentWS URL with an error response."""
    drive: DriveService = mock_service_with_cookies.drive
    mock_file = Mock()
    mock_file.name = "test_file.txt"
    mock_file.tell = Mock(side_effect=[0, 300, 0])  # Mock file size as 300 bytes

    with (
        patch.object(
            drive.session, "post", return_value=Mock(ok=False, reason="Bad Request")
        ) as mock_post,
        patch("mimetypes.guess_type", return_value=("text/plain", None)),
    ):
        with pytest.raises(PyiCloudAPIResponseException, match="Bad Request"):
            drive._get_upload_contentws_url(mock_file)

        mock_post.assert_called_once_with(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/upload/web",
            params=ANY,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json={
                "filename": "test_file.txt",
                "type": "FILE",
                "content_type": "text/plain",
                "size": 300,
            },
        )


def test_get_upload_contentws_url_invalid_response_format(
    mock_service_with_cookies: PyiCloudService,
) -> None:
    """Test retrieval of upload contentWS URL with an invalid response format."""
    drive: DriveService = mock_service_with_cookies.drive
    mock_file = Mock()
    mock_file.name = "test_file.txt"
    mock_file.tell = Mock(side_effect=[0, 400, 0])  # Mock file size as 400 bytes

    mock_response = []  # Invalid response format
    with (
        patch.object(
            drive.session,
            "post",
            return_value=Mock(ok=True, json=lambda: mock_response),
        ) as mock_post,
        patch("mimetypes.guess_type", return_value=("text/plain", None)),
    ):
        with pytest.raises(IndexError):
            drive._get_upload_contentws_url(mock_file)

        mock_post.assert_called_once_with(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/upload/web",
            params=ANY,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json={
                "filename": "test_file.txt",
                "type": "FILE",
                "content_type": "text/plain",
                "size": 400,
            },
        )


def test_send_file_success(mock_service_with_cookies: PyiCloudService) -> None:
    """Test successfully sending a file to iCloud Drive."""
    drive: DriveService = mock_service_with_cookies.drive

    mock_file = Mock()
    mock_file.name = "test_file.txt"
    mock_file.tell = Mock(side_effect=[0, 100, 0])  # Mock file size as 100 bytes

    mock_upload_url_response = [
        {"document_id": "mock_document_id", "url": "https://example.com/upload"}
    ]
    mock_upload_response = {
        "singleFile": {
            "fileChecksum": "mock_checksum",
            "wrappingKey": "mock_key",
            "referenceChecksum": "mock_reference",
            "size": 100,
        }
    }
    mock_update_response = {"status": "OK"}

    with (
        patch.object(
            drive.session,
            "post",
            side_effect=[
                Mock(
                    ok=True, json=lambda: mock_upload_url_response
                ),  # _get_upload_contentws_url
                Mock(ok=True, json=lambda: mock_upload_response),  # Upload file
                Mock(ok=True, json=lambda: mock_update_response),  # _update_contentws
            ],
        ) as mock_post,
        patch("mimetypes.guess_type", return_value=("text/plain", None)),
    ):
        drive.send_file("mock_folder_id", mock_file)

        # Assert _get_upload_contentws_url call
        mock_post.assert_any_call(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/upload/web",
            params=ANY,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json={
                "filename": "test_file.txt",
                "type": "FILE",
                "content_type": "text/plain",
                "size": 100,
            },
        )

        # Assert file upload call
        mock_post.assert_any_call(
            "https://example.com/upload",
            files={"test_file.txt": mock_file},
        )

        # Assert _update_contentws call
        mock_post.assert_any_call(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/update/documents",
            params=drive.params,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json=ANY,
        )


def test_send_file_upload_error(mock_service_with_cookies: PyiCloudService) -> None:
    """Test sending a file to iCloud Drive with an upload error."""
    drive: DriveService = mock_service_with_cookies.drive
    mock_file = Mock()
    mock_file.name = "test_file.txt"
    mock_file.tell = Mock(side_effect=[0, 100, 0])  # Mock file size as 100 bytes

    mock_upload_url_response = [
        {"document_id": "mock_document_id", "url": "https://example.com/upload"}
    ]

    with (
        patch.object(
            drive.session,
            "post",
            side_effect=[
                Mock(
                    ok=True, json=lambda: mock_upload_url_response
                ),  # _get_upload_contentws_url
                Mock(ok=False, reason="Upload Failed"),  # Upload file
            ],
        ) as mock_post,
        patch("mimetypes.guess_type", return_value=("text/plain", None)),
    ):
        with pytest.raises(PyiCloudAPIResponseException, match="Upload Failed"):
            drive.send_file("mock_folder_id", mock_file)

        # Assert _get_upload_contentws_url call
        mock_post.assert_any_call(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/upload/web",
            params=ANY,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json={
                "filename": "test_file.txt",
                "type": "FILE",
                "content_type": "text/plain",
                "size": 100,
            },
        )

        # Assert file upload call
        mock_post.assert_any_call(
            "https://example.com/upload",
            files={"test_file.txt": mock_file},
        )


def test_send_file_update_error(mock_service_with_cookies: PyiCloudService) -> None:
    """Test sending a file to iCloud Drive with an update error."""
    drive: DriveService = mock_service_with_cookies.drive
    mock_file = Mock()
    mock_file.name = "test_file.txt"
    mock_file.tell = Mock(side_effect=[0, 100, 0])  # Mock file size as 100 bytes

    mock_upload_url_response = [
        {"document_id": "mock_document_id", "url": "https://example.com/upload"}
    ]
    mock_upload_response = {
        "singleFile": {
            "fileChecksum": "mock_checksum",
            "wrappingKey": "mock_key",
            "referenceChecksum": "mock_reference",
            "size": 100,
        }
    }

    with (
        patch.object(
            drive.session,
            "post",
            side_effect=[
                Mock(
                    ok=True, json=lambda: mock_upload_url_response
                ),  # _get_upload_contentws_url
                Mock(ok=True, json=lambda: mock_upload_response),  # Upload file
                Mock(ok=False, reason="Update Failed"),  # _update_contentws
            ],
        ) as mock_post,
        patch("mimetypes.guess_type", return_value=("text/plain", None)),
    ):
        with pytest.raises(PyiCloudAPIResponseException, match="Update Failed"):
            drive.send_file("mock_folder_id", mock_file)

        # Assert _get_upload_contentws_url call
        mock_post.assert_any_call(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/upload/web",
            params=ANY,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json={
                "filename": "test_file.txt",
                "type": "FILE",
                "content_type": "text/plain",
                "size": 100,
            },
        )

        # Assert file upload call
        mock_post.assert_any_call(
            "https://example.com/upload",
            files={"test_file.txt": mock_file},
        )

        # Assert _update_contentws call
        mock_post.assert_any_call(
            drive._document_root + f"/ws/{CLOUD_DOCS_ZONE}/update/documents",
            params=drive.params,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            json=ANY,
        )
