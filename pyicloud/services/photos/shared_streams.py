"""Shared photo stream (legacy shared albums) support on CloudKit base classes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode

from requests import Response

from pyicloud.const import CONTENT_TYPE, CONTENT_TYPE_TEXT

# Runtime imports to avoid circular dependency with service.py
from .constants import (
    DirectionEnum,
    ListTypeEnum,
)

# Import base classes at runtime to break circular import
from .service import (
    AlbumContainer,
    BasePhotoAlbum,
    BasePhotoLibrary,
    PhotoAsset,
    PhotosService,
)

if TYPE_CHECKING:
    pass


class PhotoStreamLibrary(BasePhotoLibrary):
    """Represents a shared photo library (shared streams).

    Subclasses CloudKit's BasePhotoLibrary but forces client=None to skip
    typed CloudKit queries, and skips the indexing check since shared streams
    don't depend on private library indexing.
    """

    def __init__(
        self,
        service: PhotosService,
        shared_streams_url: str,
    ) -> None:
        # Force client=None and skip indexing check with check_indexing=False
        super().__init__(
            service=service,
            asset_type=PhotoStreamAsset,
            upload_url=None,
            client=None,
            check_indexing=False,
        )
        self.shared_streams_url: str = shared_streams_url

    def _get_albums(self) -> AlbumContainer:
        """Returns albums from the shared streams endpoint."""
        albums: AlbumContainer = AlbumContainer()
        url: str = f"{self.shared_streams_url}?{urlencode(self.service.params)}"
        request: Response = self.service.session.post(
            url, json={}, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )
        response: dict[str, list[dict[str, Any]]] = request.json()
        for album in response["albums"]:
            shared_stream = SharedPhotoStreamAlbum(
                library=self,
                name=album["attributes"]["name"],
                album_location=album["albumlocation"],
                album_ctag=album["albumctag"],
                album_guid=album["albumguid"],
                owner_dsid=album["ownerdsid"],
                creation_date=album["attributes"]["creationDate"],
                sharing_type=album["sharingtype"],
                allow_contributions=album["attributes"]["allowcontributions"],
                is_public=album["attributes"]["ispublic"],
                is_web_upload_supported=album["iswebuploadsupported"],
                public_url=album.get("publicurl", None),
            )
            albums.append(shared_stream)
        return albums


class SharedPhotoStreamAlbum(BasePhotoAlbum):
    """A Shared Stream Photo Album on CloudKit base."""

    def __init__(
        self,
        library: BasePhotoLibrary,
        name: str,
        album_location: str,
        album_ctag: str,
        album_guid: str,
        owner_dsid: str,
        creation_date: str,
        sharing_type: str = "owned",
        allow_contributions: bool = False,
        is_public: bool = False,
        is_web_upload_supported: bool = False,
        public_url: str | None = None,
        page_size: int = 100,
    ) -> None:
        super().__init__(
            library=library,
            name=name,
            list_type=ListTypeEnum.SHARED_STREAM,
            page_size=page_size,
            direction=DirectionEnum.ASCENDING,
        )

        self._album_location: str = album_location
        self._album_ctag: str = album_ctag
        self._album_guid: str = album_guid
        self._owner_dsid: str = owner_dsid
        try:
            self.creation_date: datetime = datetime.fromtimestamp(
                int(creation_date) / 1000.0, timezone.utc
            )
        except (TypeError, ValueError, OverflowError):
            self.creation_date = datetime.fromtimestamp(0, timezone.utc)

        # Read only properties
        self._sharing_type: str = sharing_type
        self._allow_contributions: bool = allow_contributions
        self._is_public: bool = is_public
        self._is_web_upload_supported: bool = is_web_upload_supported
        self._public_url: str | None = public_url

    @property
    def id(self) -> str:
        """Gets the album id."""
        return self._album_guid

    @property
    def fullname(self) -> str:
        return self.name

    @property
    def sharing_type(self) -> str:
        """Gets the sharing type."""
        return self._sharing_type

    @property
    def allow_contributions(self) -> bool:
        """Gets if contributions are allowed."""
        return self._allow_contributions

    @property
    def is_public(self) -> bool:
        """Gets if the album is public."""
        return self._is_public

    @property
    def is_web_upload_supported(self) -> bool:
        """Gets if web uploads are supported."""
        return self._is_web_upload_supported

    @property
    def public_url(self) -> str | None:
        """Gets the public URL."""
        return self._public_url

    def _get_payload(
        self, offset: int, page_size: int, direction: DirectionEnum
    ) -> dict[str, Any]:
        return {
            "albumguid": self._album_guid,
            "albumctag": self._album_ctag,
            "limit": str(offset + page_size),
            "offset": str(offset),
        }

    def _get_photo_payload(self, photo_id: str) -> dict[str, Any]:
        # For shared streams, avoid building a payload that explicitly requests
        # the entire album based on len(self). The actual lookup-by-id logic is
        # implemented in _get_photo(), which pages through results as needed.
        raise NotImplementedError(
            "_get_photo_payload is not implemented for SharedPhotoStreamAlbum"
        )

    def _get_photo(self, photo_id: str) -> PhotoAsset:
        """
        Fetch a single photo by id by paging through the shared stream.
        This avoids an upfront call to get the album size and does not
        require fetching the entire album in one request.
        """
        offset: int = 0
        while True:
            page = self._get_photos_at(offset, DirectionEnum.ASCENDING, self.page_size)
            photo_count = 0
            for photo in page:
                photo_count += 1
                if photo.id == photo_id:
                    return photo
            if photo_count < self.page_size:
                break
            offset += photo_count
        raise KeyError(f"Photo does not exist: {photo_id}")

    def _get_url(self) -> str:
        return f"{self._album_location}webgetassets?{urlencode(self.service.params)}"

    def _get_len(self) -> int:
        url: str = (
            f"{self._album_location}webgetassetcount?{urlencode(self.service.params)}"
        )
        request: Response = self.service.session.post(
            url,
            json={
                "albumguid": self._album_guid,
            },
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response: dict[str, Any] = request.json()

        return cast(int, response["albumassetcount"])

    def delete(self) -> bool:
        """Deletes the album - shared albums cannot be deleted."""
        return False

    def rename(self, value: str) -> None:
        """Renames the album - shared albums cannot be renamed."""
        return None


class PhotoStreamAsset(PhotoAsset):
    """A Shared Stream Photo Asset with overridden
    properties for shared-stream responses."""

    @property
    def like_count(self) -> int:
        """Gets the photo like count from pluginFields."""
        fields = self._asset_record.fields
        raw_plugin_fields: Any = (
            fields.get("pluginFields") if hasattr(fields, "get") else {}
        )
        plugin_fields: dict[str, Any] = raw_plugin_fields
        if not isinstance(plugin_fields, dict):
            return 0
        like_count = plugin_fields.get("likeCount")
        if not isinstance(like_count, dict):
            return 0
        value = like_count.get("value", 0)
        return int(value) if value is not None else 0

    @property
    def liked(self) -> bool:
        """Gets if the photo is liked from pluginFields."""
        fields = self._asset_record.fields
        raw_plugin_fields: Any = (
            fields.get("pluginFields") if hasattr(fields, "get") else {}
        )
        plugin_fields: dict[str, Any] = raw_plugin_fields
        if not isinstance(plugin_fields, dict):
            return False
        liked_by_caller = plugin_fields.get("likedByCaller")
        if not isinstance(liked_by_caller, dict):
            return False
        value = liked_by_caller.get("value", False)
        return bool(value)

    @property
    def asset_date(self) -> datetime:
        """Gets the photo asset date from master record's originalCreationDate."""
        fields = self._master_record.fields
        value = fields.get("originalCreationDate") if hasattr(fields, "get") else None
        if isinstance(value, datetime):
            return value
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000.0, timezone.utc)
        return datetime.fromtimestamp(0, timezone.utc)

    @property
    def size(self) -> int:
        """Gets the photo asset size from master record's resOriginalFileSize."""
        try:
            fields = self._master_record.fields
            res_size = (
                fields.get("resOriginalFileSize") if hasattr(fields, "get") else None
            )
            if res_size and isinstance(res_size, dict):
                value = res_size.get("value", 0)
                return int(value) if value is not None else 0
            if res_size is not None and hasattr(res_size, "value"):
                value = res_size.value
                return int(value) if value is not None else 0
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        return 0
