"""Drive service."""

import io
import json
import logging
import mimetypes
import os
import time
import uuid
from datetime import datetime, timedelta
from re import Match, search
from typing import Any, Optional

from requests import Response

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT
from pyicloud.exceptions import PyiCloudAPIResponseException, TokenException
from pyicloud.services.base import BaseService
from pyicloud.session import PyiCloudSession

LOGGER: logging.Logger = logging.getLogger(__name__)

COOKIE_APPLE_WEBAUTH_VALIDATE = "X-APPLE-WEBAUTH-VALIDATE"
CLOUD_DOCS = "/ws/com.apple.CloudDocs"


class DriveService(BaseService):
    """The 'Drive' iCloud service."""

    def __init__(
        self,
        service_root: str,
        document_root: str,
        session: PyiCloudSession,
        params: dict[str, Any],
    ) -> None:
        super().__init__(service_root, session, params)
        self._document_root: str = document_root
        self._root: Optional[DriveNode] = None
        self._trash: Optional[DriveNode] = None

    def _get_token_from_cookie(self) -> dict[str, Any]:
        for cookie in self.session.cookies:
            if cookie.name == COOKIE_APPLE_WEBAUTH_VALIDATE and cookie.value:
                match: Optional[Match[str]] = search(r"\bt=([^:]+)", cookie.value)
                if match is None:
                    raise TokenException("Can't extract token from %r" % cookie.value)
                return {"token": match.group(1)}
        raise TokenException("Token cookie not found")

    def get_node_data(self, node_id):
        """Returns the node data."""
        request: Response = self.session.post(
            self.service_root + "/retrieveItemDetailsInFolders",
            params=self.params,
            data=json.dumps(
                [
                    {
                        "drivewsid": "FOLDER::com.apple.CloudDocs::%s" % node_id,
                        "partialData": False,
                    }
                ]
            ),
        )
        self._raise_if_error(request)
        return request.json()[0]

    def custom_request(self, method, path, data=None):
        """Raw function to allow for custom requests"""
        request: Response = self.session.request(
            method,
            self.service_root + f"/{path}",
            params=self.params,
            data=json.dumps(data) if data else None,
        )
        self._raise_if_error(request)
        return request.json()

    def get_file(self, file_id, **kwargs) -> Response:
        """Returns iCloud Drive file."""
        file_params = dict(self.params)
        file_params.update({"document_id": file_id})
        response: Response = self.session.get(
            self._document_root + f"{CLOUD_DOCS}/download/by_id",
            params=file_params,
        )
        self._raise_if_error(response)
        response_json = response.json()
        package_token = response_json.get("package_token")
        data_token = response_json.get("data_token")
        if data_token and data_token.get("url"):
            return self.session.get(data_token["url"], params=self.params, **kwargs)
        if package_token and package_token.get("url"):
            return self.session.get(package_token["url"], params=self.params, **kwargs)
        raise KeyError("'data_token' nor 'package_token'")

    def get_app_data(self):
        """Returns the app library (previously ubiquity)."""
        request: Response = self.session.get(
            self.service_root + "/retrieveAppLibraries", params=self.params
        )
        self._raise_if_error(request)
        return request.json()["items"]

    def _get_upload_contentws_url(self, file_object):
        """Get the contentWS endpoint URL to add a new file."""
        content_type: Optional[str] = mimetypes.guess_type(file_object.name)[0]
        if content_type is None:
            content_type = ""

        # Get filesize from file object
        orig_pos: int = file_object.tell()
        file_object.seek(0, os.SEEK_END)
        file_size: int = file_object.tell()
        file_object.seek(orig_pos, os.SEEK_SET)

        file_params: dict[str, Any] = self.params
        file_params.update(self._get_token_from_cookie())

        request: Response = self.session.post(
            self._document_root + f"{CLOUD_DOCS}/upload/web",
            params=file_params,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            data=json.dumps(
                {
                    "filename": file_object.name,
                    "type": "FILE",
                    "content_type": content_type,
                    "size": file_size,
                }
            ),
        )
        self._raise_if_error(request)
        return (request.json()[0]["document_id"], request.json()[0]["url"])

    def _update_contentws(self, folder_id, sf_info, document_id, file_object):
        data: dict[str, Any] = {
            "data": {
                "signature": sf_info["fileChecksum"],
                "wrapping_key": sf_info["wrappingKey"],
                "reference_signature": sf_info["referenceChecksum"],
                "size": sf_info["size"],
            },
            "command": "add_file",
            "create_short_guid": True,
            "document_id": document_id,
            "path": {
                "starting_document_id": folder_id,
                "path": os.path.basename(file_object.name),
            },
            "allow_conflict": True,
            "file_flags": {
                "is_writable": True,
                "is_executable": False,
                "is_hidden": False,
            },
            "mtime": int(time.time() * 1000),
            "btime": int(time.time() * 1000),
        }

        # Add the receipt if we have one. Will be absent for 0-sized files
        if sf_info.get("receipt"):
            data["data"].update({"receipt": sf_info["receipt"]})

        request: Response = self.session.post(
            self._document_root + f"{CLOUD_DOCS}/update/documents",
            params=self.params,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            data=json.dumps(data),
        )
        self._raise_if_error(request)
        return request.json()

    def send_file(self, folder_id, file_object) -> None:
        """Send new file to iCloud Drive."""
        document_id, content_url = self._get_upload_contentws_url(file_object)

        request: Response = self.session.post(
            content_url, files={file_object.name: file_object}
        )
        self._raise_if_error(request)
        content_response = request.json()["singleFile"]
        self._update_contentws(folder_id, content_response, document_id, file_object)

    def create_folders(self, parent, name):
        """Creates a new iCloud Drive folder"""
        # when creating a folder on icloud.com, the clientID is set to the following:
        temp_client_id: str = f"FOLDER::UNKNOWN_ZONE::TempId-{uuid.uuid4()}"
        request: Response = self.session.post(
            self.service_root + "/createFolders",
            params=self.params,
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            data=json.dumps(
                {
                    "destinationDrivewsId": parent,
                    "folders": [
                        {
                            "clientId": temp_client_id,
                            "name": name,
                        }
                    ],
                }
            ),
        )
        self._raise_if_error(request)
        return request.json()

    def rename_items(self, node_id, etag, name):
        """Renames an iCloud Drive node"""
        request: Response = self.session.post(
            self.service_root + "/renameItems",
            params=self.params,
            data=json.dumps(
                {
                    "items": [
                        {
                            "drivewsid": node_id,
                            "etag": etag,
                            "name": name,
                        }
                    ],
                }
            ),
        )
        self._raise_if_error(request)
        return request.json()

    def move_items_to_trash(self, node_id, etag):
        """Moves an iCloud Drive node to the trash bin"""
        # when moving a node to the trash on icloud.com, the clientID is set to the node_id:
        temp_client_id = node_id
        request: Response = self.session.post(
            self.service_root + "/moveItemsToTrash",
            params=self.params,
            data=json.dumps(
                {
                    "items": [
                        {
                            "drivewsid": node_id,
                            "etag": etag,
                            "clientId": temp_client_id,
                        }
                    ],
                }
            ),
        )
        self._raise_if_error(request)
        return request.json()

    def recover_items_from_trash(self, node_id, etag):
        """Restores an iCloud Drive node from the trash bin"""
        request = self.session.post(
            self.service_root + "/putBackItemsFromTrash",
            params=self.params,
            data=json.dumps(
                {
                    "items": [{"drivewsid": node_id, "etag": etag}],
                }
            ),
        )
        self._raise_if_error(request)
        return request.json()

    def delete_forever_from_trash(self, node_id, etag):
        """Permanently deletes an iCloud Drive node from the trash bin"""
        request: Response = self.session.post(
            self.service_root + "/deleteItems",
            params=self.params,
            data=json.dumps(
                {
                    "items": [{"drivewsid": node_id, "etag": etag}],
                }
            ),
        )
        self._raise_if_error(request)
        return request.json()

    @property
    def root(self) -> "DriveNode":
        """Returns the root node."""
        if not self._root:
            self._root = DriveNode(self, self.get_node_data("root"))
        return self._root

    @property
    def trash(self) -> "DriveNode":
        """Returns the trash node."""
        if not self._trash:
            self._trash = DriveNode(self, self.get_node_data("TRASH_ROOT"))
        return self._trash

    def refresh_root(self) -> "DriveNode":
        """Refreshes and returns a fresh root node."""
        self._root = DriveNode(self, self.get_node_data("root"))
        return self._root

    def refresh_trash(self) -> "DriveNode":
        """Refreshes and returns a fresh trash node."""
        self._trash = DriveNode(self, self.get_node_data("TRASH_ROOT"))
        return self._trash

    def __getattr__(self, attr):
        return getattr(self.root, attr)

    def __getitem__(self, key) -> Optional["DriveNode"]:
        return self.root[key]

    @staticmethod
    def _raise_if_error(response: Response) -> None:
        if not response.ok:
            api_error = PyiCloudAPIResponseException(
                response.reason, response.status_code
            )
            LOGGER.error(api_error)
            raise api_error


class DriveNode:
    """Drive node."""

    def __init__(self, conn, data) -> None:
        self.data = data
        self.connection = conn
        self._children: Optional[list[DriveNode]] = None

    @property
    def name(self) -> str:
        """Gets the node name."""
        # check if name is undefined, return drivewsid instead if so.
        node_name: Optional[str] = self.data.get("name")
        if not node_name:
            # use drivewsid as name if no name present.
            node_name = self.data.get("drivewsid")
            # Clean up well-known drivewsid names
            if node_name == "FOLDER::com.apple.CloudDocs::root":
                node_name = "root"
            # if no name still, return unknown string.
            if not node_name:
                node_name = "<UNKNOWN>"

        if "extension" in self.data:
            return f"{node_name}.{self.data['extension']}"
        return node_name

    @property
    def type(self) -> Optional[str]:
        """Gets the node type."""
        node_type: Optional[str] = self.data.get("type")
        # handle trash which has no node type
        if not node_type and self.data.get("drivewsid") == "TRASH_ROOT":
            node_type = "trash"
        return node_type and node_type.lower()

    def get_children(self) -> list["DriveNode"]:
        """Gets the node children."""
        if not self._children:
            if "items" not in self.data:
                self.data.update(self.connection.get_node_data(self.data["docwsid"]))
            if "items" not in self.data:
                raise KeyError("No items in folder, status: %s" % self.data["status"])
            self._children = [
                DriveNode(self.connection, item_data)
                for item_data in self.data["items"]
            ]
        return self._children

    @property
    def size(self) -> Optional[int]:
        """Gets the node size."""
        size: Optional[str] = self.data.get("size")  # Folder does not have size
        if not size:
            return None
        return int(size)

    @property
    def date_changed(self) -> Optional[datetime]:
        """Gets the node changed date (in UTC)."""
        return _date_to_utc(self.data.get("dateChanged"))  # Folder does not have date

    @property
    def date_modified(self) -> Optional[datetime]:
        """Gets the node modified date (in UTC)."""
        return _date_to_utc(self.data.get("dateModified"))  # Folder does not have date

    @property
    def date_last_open(self) -> Optional[datetime]:
        """Gets the node last open date (in UTC)."""
        return _date_to_utc(self.data.get("lastOpenTime"))  # Folder does not have date

    def open(self, **kwargs):
        """Gets the node file."""
        # iCloud returns 400 Bad Request for 0-byte files
        if self.data["size"] == 0:
            response = Response()
            response.raw = io.BytesIO()
            return response
        return self.connection.get_file(self.data["docwsid"], **kwargs)

    def upload(self, file_object, **kwargs):
        """Upload a new file."""
        return self.connection.send_file(self.data["docwsid"], file_object, **kwargs)

    def dir(self) -> Optional[list[str]]:
        """Gets the node list of directories."""
        if self.type == "file":
            return None
        return [child.name for child in self.get_children()]

    def mkdir(self, folder):
        """Create a new directory directory."""
        return self.connection.create_folders(self.data["drivewsid"], folder)

    def rename(self, name):
        """Rename an iCloud Drive item."""
        return self.connection.rename_items(
            self.data["drivewsid"], self.data["etag"], name
        )

    def delete(self):
        """Delete an iCloud Drive item."""
        return self.connection.move_items_to_trash(
            self.data["drivewsid"], self.data["etag"]
        )

    def recover(self):
        """Recovers an iCloud Drive item from trash."""
        # check to ensure item is in the trash - it should have a "restorePath" property
        if self.data.get("restorePath"):
            return self.connection.recover_items_from_trash(
                self.data["drivewsid"], self.data["etag"]
            )
        else:
            raise ValueError(f"'{self.name}' does not appear to be in the Trash.")

    def delete_forever(self):
        """Permanently deletes an iCloud Drive item from trash."""
        # check to ensure item is in the trash - it should have a "restorePath" property
        if self.data.get("restorePath"):
            return self.connection.delete_forever_from_trash(
                self.data["drivewsid"], self.data["etag"]
            )
        else:
            raise ValueError(
                f"'{self.name}' does not appear to be in the Trash. Please 'delete()' it first before "
                f"trying to 'delete_forever()'."
            )

    def get(self, name) -> Optional["DriveNode"]:
        """Gets the node child."""
        if self.type == "file":
            return None
        return [child for child in self.get_children() if child.name == name][0]

    def __getitem__(self, key) -> Optional["DriveNode"]:
        try:
            return self.get(key)
        except IndexError as i:
            raise KeyError(f"No child named '{key}' exists") from i

    def __str__(self) -> str:
        return "{" + f"type: {self.type}, name: {self.name}" + "}"

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {str(self)}>"


def _date_to_utc(date) -> Optional[datetime]:
    if not date:
        return None
    # jump through hoops to return time in UTC rather than California time
    match: Optional[Match[str]] = search(r"^(.+?)([\+\-]\d+):(\d\d)$", date)
    if not match:
        # Already in UTC
        return datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")
    base: datetime = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    diff = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
    return base - diff
