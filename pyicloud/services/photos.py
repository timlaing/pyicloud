"""Photo service."""

import base64
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlencode

from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudServiceNotActivatedException,
)

from ..const import CONTENT_TYPE, CONTENT_TYPE_TEXT


class PhotoLibrary:
    """Represents a library in the user's photos.

    This provides access to all the albums as well as the photos.
    """

    SMART_FOLDERS = {
        "All Photos": {
            "obj_type": "CPLAssetByAssetDateWithoutHiddenOrDeleted",
            "list_type": "CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted",
            "direction": "ASCENDING",
            "query_filter": None,
        },
        "Time-lapse": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Timelapse",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "TIMELAPSE"},
                }
            ],
        },
        "Videos": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Video",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "VIDEO"},
                }
            ],
        },
        "Slo-mo": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Slomo",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "SLOMO"},
                }
            ],
        },
        "Bursts": {
            "obj_type": "CPLAssetBurstStackAssetByAssetDate",
            "list_type": "CPLBurstStackAssetAndMasterByAssetDate",
            "direction": "ASCENDING",
            "query_filter": None,
        },
        "Favorites": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Favorite",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "FAVORITE"},
                }
            ],
        },
        "Panoramas": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Panorama",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "PANORAMA"},
                }
            ],
        },
        "Screenshots": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Screenshot",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "SCREENSHOT"},
                }
            ],
        },
        "Live": {
            "obj_type": "CPLAssetInSmartAlbumByAssetDate:Live",
            "list_type": "CPLAssetAndMasterInSmartAlbumByAssetDate",
            "direction": "ASCENDING",
            "query_filter": [
                {
                    "fieldName": "smartAlbum",
                    "comparator": "EQUALS",
                    "fieldValue": {"type": "STRING", "value": "LIVE"},
                }
            ],
        },
        "Recently Deleted": {
            "obj_type": "CPLAssetDeletedByExpungedDate",
            "list_type": "CPLAssetAndMasterDeletedByExpungedDate",
            "direction": "ASCENDING",
            "query_filter": None,
        },
        "Hidden": {
            "obj_type": "CPLAssetHiddenByAssetDate",
            "list_type": "CPLAssetAndMasterHiddenByAssetDate",
            "direction": "ASCENDING",
            "query_filter": None,
        },
    }

    def __init__(self, service, zone_id, upload_url=None) -> None:
        self.service = service
        self._upload_url = upload_url
        self.zone_id = zone_id
        self._albums = None

        url = f"{self.service.service_endpoint}/records/query?{urlencode(self.service.params)}"
        json_data = json.dumps(
            {"query": {"recordType": "CheckIndexingState"}, "zoneID": self.zone_id}
        )
        request = self.service.session.post(
            url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )
        response = request.json()
        indexing_state = response["records"][0]["fields"]["state"]["value"]
        if indexing_state != "FINISHED":
            raise PyiCloudServiceNotActivatedException(
                "iCloud Photo Library not finished indexing. "
                "Please try again in a few minutes."
            )

    @property
    def albums(self):
        """Returns photo albums."""
        if not self._albums:
            self._albums = {
                name: PhotoAlbum(self.service, name, zone_id=self.zone_id, **props)
                for (name, props) in self.SMART_FOLDERS.items()
            }

            for folder in self._fetch_folders():
                # Skiping albums having null name, that can happen sometime
                if "albumNameEnc" not in folder["fields"]:
                    continue

                if folder["recordName"] in (
                    "----Root-Folder----",
                    "----Project-Root-Folder----",
                ) or (
                    folder["fields"].get("isDeleted")
                    and folder["fields"]["isDeleted"]["value"]
                ):
                    continue

                folder_id = folder["recordName"]
                folder_obj_type = (
                    "CPLContainerRelationNotDeletedByAssetDate:%s" % folder_id
                )
                folder_name = base64.b64decode(
                    folder["fields"]["albumNameEnc"]["value"]
                ).decode("utf-8")
                query_filter = [
                    {
                        "fieldName": "parentId",
                        "comparator": "EQUALS",
                        "fieldValue": {"type": "STRING", "value": folder_id},
                    }
                ]

                album = PhotoAlbum(
                    self.service,
                    folder_name,
                    "CPLContainerRelationLiveByAssetDate",
                    folder_obj_type,
                    "ASCENDING",
                    query_filter,
                    zone_id=self.zone_id,
                )
                self._albums[folder_name] = album

        return self._albums

    def _fetch_folders(self):
        url = f"{self.service.service_endpoint}/records/query?{urlencode(self.service.params)}"
        json_data = json.dumps(
            {"query": {"recordType": "CPLAlbumByPositionLive"}, "zoneID": self.zone_id}
        )

        request = self.service.session.post(
            url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )
        response = request.json()

        return response["records"]

    @property
    def all(self):
        """Returns all photos."""
        return self.albums["All Photos"]

    def upload_file(self, path):
        """Upload a photo from path, returns a recordName"""

        filename = os.path.basename(path)
        url = "{}/upload".format(self._upload_url)

        with open(path, "rb") as file_obj:
            request = self.service.session.post(
                url,
                data=file_obj.read(),
                params={
                    "filename": filename,
                    "dsid": self.service.params["dsid"],
                },
            )

        if "errors" in request.json():
            raise PyiCloudAPIResponseException("", request.json()["errors"])

        return [
            x["recordName"]
            for x in request.json()["records"]
            if x["recordType"] == "CPLAsset"
        ][0]


class PhotosService(PhotoLibrary):
    """The 'Photos' iCloud service.

    This also acts as a way to access the user's primary library."""

    def __init__(self, service_root, session, params, upload_url, shared_streams_url):
        self.session = session
        self.params = dict(params)
        self._service_root = service_root
        self.service_endpoint = (
            "%s/database/1/com.apple.photos.cloud/production/private"
            % self._service_root
        )
        self._shared_streams_url = shared_streams_url
        self.shared_streams_url = f"{self._shared_streams_url}/{self.params['dsid']}/sharedstreams/webgetalbumslist"
        self._shared_streams = None

        self._libraries = None

        self.params.update({"remapEnums": True, "getCurrentSyncToken": True})

        self._photo_assets = {}

        super().__init__(
            service=self,
            upload_url=upload_url,
            zone_id={"zoneName": "PrimarySync"},
        )

    @property
    def shared_streams(self):
        if not self._shared_streams:
            self._shared_streams = dict()
            url = f"{self.shared_streams_url}?{urlencode(self.service.params)}"
            # print(url)
            json_data = json.dumps({})
            request = self.service.session.post(
                url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
            )
            response = request.json()
            for album in response['albums']:
                shared_stream = SharedStream(service=self.service,
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
                                             public_url=album.get("publicurl", None))
                self._shared_streams[album['attributes']['name']] = shared_stream
        return self._shared_streams

    @property
    def libraries(self):
        if not self._libraries:
            url = "%s/changes/database" % (self.service_endpoint,)

            request = self.session.post(
                url, data="{}", headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
            )
            response = request.json()
            zones = response["zones"]

            libraries = {}
            for zone in zones:
                if not zone.get("deleted"):
                    zone_name = zone["zoneID"]["zoneName"]
                    libraries[zone_name] = PhotoLibrary(self, zone["zoneID"])

            self._libraries = libraries

        return self._libraries


class PhotoAlbum:
    """A photo album."""

    def __init__(
        self,
        service,
        name,
        list_type,
        obj_type,
        direction,
        query_filter=None,
        page_size=100,
        zone_id=None,
    ):
        self.name = name
        self.service = service
        self.list_type = list_type
        self.obj_type = obj_type
        self.direction = direction
        self.query_filter = query_filter
        self.page_size = page_size

        if zone_id:
            self.zone_id = zone_id
        else:
            self.zone_id = {"zoneName": "PrimarySync"}

        self._len = None

    @property
    def title(self):
        """Gets the album name."""
        return self.name

    def __iter__(self):
        return self.photos

    def __len__(self):
        if self._len is None:
            url = "{}/internal/records/query/batch?{}".format(
                self.service.service_endpoint,
                urlencode(self.service.params),
            )
            request = self.service.session.post(
                url,
                data=json.dumps(
                    {
                        "batch": [
                            {
                                "resultsLimit": 1,
                                "query": {
                                    "filterBy": {
                                        "fieldName": "indexCountID",
                                        "fieldValue": {
                                            "type": "STRING_LIST",
                                            "value": [self.obj_type],
                                        },
                                        "comparator": "IN",
                                    },
                                    "recordType": "HyperionIndexCountLookup",
                                },
                                "zoneWide": True,
                                "zoneID": self.zone_id,
                            }
                        ]
                    }
                ),
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            response = request.json()

            self._len = response["batch"][0]["records"][0]["fields"]["itemCount"][
                "value"
            ]

        return self._len

    @property
    def photos(self):
        """Returns the album photos."""
        if self.direction == "DESCENDING":
            offset = len(self) - 1
        else:
            offset = 0

        while True:
            num_results = 0
            for photo in self._get_photos_at(
                offset, self.direction, self.page_size * 2
            ):
                num_results += 1
                yield photo
            if num_results == 0:
                break
            if self.direction == "DESCENDING":
                offset = offset - num_results
            else:
                offset = offset + num_results

    def photo(self, index):
        return self._get_photos_at(index, self.direction, 2)

    def _get_photos_at(self, index, direction, page_size=100):
        if direction == "DESCENDING":
            offset = len(self) - index - 1
        else:
            offset = index

        url = ("%s/records/query?" % self.service.service_endpoint) + urlencode(
            self.service.params
        )
        request = self.service.session.post(
            url,
            data=json.dumps(
                self._list_query_gen(
                    index, self.list_type, direction, page_size, self.query_filter
                )
            ),
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response = request.json()

        asset_records = {}
        master_records = []
        for rec in response["records"]:
            if rec["recordType"] == "CPLAsset":
                master_id = rec["fields"]["masterRef"]["value"]["recordName"]
                asset_records[master_id] = rec
            elif rec["recordType"] == "CPLMaster":
                master_records.append(rec)

        master_records_len = len(master_records)
        if master_records_len:
            if direction == "DESCENDING":
                offset = offset - master_records_len
            else:
                offset = offset + master_records_len

            for master_record in master_records:
                record_name = master_record["recordName"]
                yield PhotoAsset(
                    self.service, master_record, asset_records[record_name]
                )

    def _list_query_gen(
        self, offset, list_type, direction, num_results, query_filter=None
    ):
        query = {
            "query": {
                "filterBy": [
                    {
                        "fieldName": "startRank",
                        "fieldValue": {"type": "INT64", "value": offset},
                        "comparator": "EQUALS",
                    },
                    {
                        "fieldName": "direction",
                        "fieldValue": {"type": "STRING", "value": direction},
                        "comparator": "EQUALS",
                    },
                ],
                "recordType": list_type,
            },
            "resultsLimit": num_results,
            "desiredKeys": [
                "resJPEGFullWidth",
                "resJPEGFullHeight",
                "resJPEGFullFileType",
                "resJPEGFullFingerprint",
                "resJPEGFullRes",
                "resJPEGLargeWidth",
                "resJPEGLargeHeight",
                "resJPEGLargeFileType",
                "resJPEGLargeFingerprint",
                "resJPEGLargeRes",
                "resJPEGMedWidth",
                "resJPEGMedHeight",
                "resJPEGMedFileType",
                "resJPEGMedFingerprint",
                "resJPEGMedRes",
                "resJPEGThumbWidth",
                "resJPEGThumbHeight",
                "resJPEGThumbFileType",
                "resJPEGThumbFingerprint",
                "resJPEGThumbRes",
                "resVidFullWidth",
                "resVidFullHeight",
                "resVidFullFileType",
                "resVidFullFingerprint",
                "resVidFullRes",
                "resVidMedWidth",
                "resVidMedHeight",
                "resVidMedFileType",
                "resVidMedFingerprint",
                "resVidMedRes",
                "resVidSmallWidth",
                "resVidSmallHeight",
                "resVidSmallFileType",
                "resVidSmallFingerprint",
                "resVidSmallRes",
                "resSidecarWidth",
                "resSidecarHeight",
                "resSidecarFileType",
                "resSidecarFingerprint",
                "resSidecarRes",
                "itemType",
                "dataClassType",
                "filenameEnc",
                "originalOrientation",
                "resOriginalWidth",
                "resOriginalHeight",
                "resOriginalFileType",
                "resOriginalFingerprint",
                "resOriginalRes",
                "resOriginalAltWidth",
                "resOriginalAltHeight",
                "resOriginalAltFileType",
                "resOriginalAltFingerprint",
                "resOriginalAltRes",
                "resOriginalVidComplWidth",
                "resOriginalVidComplHeight",
                "resOriginalVidComplFileType",
                "resOriginalVidComplFingerprint",
                "resOriginalVidComplRes",
                "isDeleted",
                "isExpunged",
                "dateExpunged",
                "remappedRef",
                "recordName",
                "recordType",
                "recordChangeTag",
                "masterRef",
                "adjustmentRenderType",
                "assetDate",
                "addedDate",
                "isFavorite",
                "isHidden",
                "orientation",
                "duration",
                "assetSubtype",
                "assetSubtypeV2",
                "assetHDRType",
                "burstFlags",
                "burstFlagsExt",
                "burstId",
                "captionEnc",
                "locationEnc",
                "locationV2Enc",
                "locationLatitude",
                "locationLongitude",
                "adjustmentType",
                "timeZoneOffset",
                "vidComplDurValue",
                "vidComplDurScale",
                "vidComplDispValue",
                "vidComplDispScale",
                "vidComplVisibilityState",
                "customRenderedValue",
                "containerId",
                "itemId",
                "position",
                "isKeyAsset",
            ],
            "zoneID": self.zone_id,
        }

        if query_filter:
            query["query"]["filterBy"].extend(query_filter)

        return query

    def __str__(self):
        return self.title

    def __repr__(self):
        return f"<{type(self).__name__}: '{self}'>"


class SharedStream:
    """A Shared Stream Photo Album."""

    def __init__(
            self,
            service,
            name,
            album_location,
            album_ctag,
            album_guid,
            owner_dsid,
            creation_date,
            sharing_type="owned",
            allow_contributions=False,
            is_public=False,
            is_web_upload_supported=False,
            public_url=None,
            page_size = 100,
    ):
        self.name = name
        self.service = service
        self.page_size = page_size
        self._album_location = album_location
        self._album_ctag = album_ctag
        self.album_guid = album_guid
        self._owner_dsid = owner_dsid
        try:
            self.creation_date = datetime.fromtimestamp(int(creation_date) / 1000.0, timezone.utc)
        except ValueError:
            self.creation_date = datetime.fromtimestamp(0,timezone.utc)

        # Read only properties
        self._sharing_type = sharing_type
        self._allow_contributions = allow_contributions
        self._is_public = is_public
        self._is_web_upload_supported = is_web_upload_supported
        self._public_url = public_url
        self._photos = None

        self._len = None

    @property
    def sharing_type(self):
        return self._sharing_type

    @property
    def allow_contributions(self):
        return self._allow_contributions

    @property
    def is_public(self):
        return self._is_public

    @property
    def is_web_upload_supported(self):
        return self._is_web_upload_supported

    @property
    def public_url(self):
        return self._public_url

    @property
    def photos(self):
        offset = 0
        while True:
            num_results = 0
            for photo in self._get_photos_at(
                    offset, self.page_size
            ):
                num_results += 1
                yield photo
            if num_results == 0:
                break
            offset = offset + num_results

    def _get_photos_at(self, offset, page_size=100):
        url = f"{self._album_location}webgetassets?{urlencode(self.service.params)}"
        limit = min(offset+page_size, len(self))
        payload = {"albumguid": self.album_guid,
                 "albumctag": self._album_ctag,
                 "limit": str(limit),
                 "offset": str(offset)}
        request = self.service.session.post(
            url,
            data=json.dumps(payload),
            headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
        )
        response = request.json()

        asset_records = {}
        master_records = []
        names = set()
        for rec in response.get("records", {}):
            if rec.get("recordType", "") == "CPLAsset":
                master_id = rec.get("fields", {}).get("masterRef",{}).get("value",{}).get("recordName", None)
                if master_id:
                    asset_records[master_id] = rec
            elif rec.get("recordType", "") == "CPLMaster":
                name = rec.get("recordName", None)
                if name and (name not in names):
                    master_records.append(rec)
                    names.add(name)

        for master_record in master_records:
            record_name = master_record.get("recordName", None)
            asset_record = asset_records.get(record_name, None)
            if record_name and asset_record:
                yield PhotoStreamAsset(self.service, master_record, asset_record)
            else:
                continue

    def __iter__(self):
        return self.photos

    def __len__(self):
        if self._len is None:
            url = f"{self._album_location}webgetassetcount?{urlencode(self.service.params)}"
            request = self.service.session.post(
                url,
                data=json.dumps(
                    {"albumguid": self.album_guid}
                ),
                headers={CONTENT_TYPE: CONTENT_TYPE_TEXT},
            )
            response = request.json()

            self._len = response["albumassetcount"]

        return self._len

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<{type(self).__name__}: '{self}'>"


class PhotoAsset:
    """A photo."""

    def __init__(self, service, master_record, asset_record):
        self._service = service
        self._master_record = master_record
        self._asset_record = asset_record

        self._versions = None

    ITEM_TYPES = {
        "public.heic": "image",
        "public.jpeg": "image",
        "public.png": "image",
        "com.apple.quicktime-movie": "movie",
    }

    PHOTO_VERSION_LOOKUP = {
        "original": "resOriginal",
        "medium": "resJPEGMed",
        "thumb": "resJPEGThumb",
    }

    VIDEO_VERSION_LOOKUP = {
        "original": "resOriginal",
        "medium": "resVidMed",
        "thumb": "resVidSmall",
    }

    @property
    def id(self):
        """Gets the photo id."""
        return self._master_record["recordName"]

    @property
    def filename(self):
        """Gets the photo file name."""
        return base64.b64decode(
            self._master_record["fields"]["filenameEnc"]["value"]
        ).decode("utf-8")

    @property
    def size(self):
        """Gets the photo size."""
        return self._master_record["fields"]["resOriginalRes"]["value"]["size"]

    @property
    def created(self):
        """Gets the photo created date."""
        return self.asset_date

    @property
    def asset_date(self):
        """Gets the photo asset date."""
        try:
            return datetime.fromtimestamp(
                self._asset_record["fields"]["assetDate"]["value"] / 1000.0,
                timezone.utc,
            )
        except KeyError:
            return datetime.fromtimestamp(0, timezone.utc)

    @property
    def added_date(self):
        """Gets the photo added date."""
        return datetime.fromtimestamp(
            self._asset_record["fields"]["addedDate"]["value"] / 1000.0, timezone.utc
        )

    @property
    def dimensions(self):
        """Gets the photo dimensions."""
        return (
            self._master_record["fields"]["resOriginalWidth"]["value"],
            self._master_record["fields"]["resOriginalHeight"]["value"],
        )

    @property
    def item_type(self):
        try:
            item_type = self._master_record["fields"]["itemType"]["value"]
        except KeyError:
            try:
                item_type = self._master_record["fields"]["resOriginalFileType"]["value"]
            except KeyError:
                return "image"
        if item_type in self.ITEM_TYPES:
            return self.ITEM_TYPES[item_type]
        if self.filename.lower().endswith((".heic", ".png", ".jpg", ".jpeg")):
            return "image"
        return "movie"

    @property
    def versions(self):
        """Gets the photo versions."""
        if not self._versions:
            self._versions = {}
            if self.item_type == "movie":
                typed_version_lookup = self.VIDEO_VERSION_LOOKUP
            else:
                typed_version_lookup = self.PHOTO_VERSION_LOOKUP

            for key, prefix in typed_version_lookup.items():
                if "%sRes" % prefix in self._master_record["fields"]:
                    self._versions[key] = self._get_photo_version(prefix)

        return self._versions

    def _get_photo_version(self, prefix):
        version: dict = {"filename": self.filename}
        fields = self._master_record["fields"]
        width_entry = fields.get("%sWidth" % prefix)
        if width_entry:
            version["width"] = width_entry["value"]
        else:
            version["width"] = None

        height_entry = fields.get("%sHeight" % prefix)
        if height_entry:
            version["height"] = height_entry["value"]
        else:
            version["height"] = None

        size_entry = fields.get("%sRes" % prefix)
        if size_entry:
            version["size"] = size_entry["value"]["size"]
            version["url"] = size_entry["value"]["downloadURL"]
        else:
            version["size"] = None
            version["url"] = None

        type_entry = fields.get("%sFileType" % prefix)
        if type_entry:
            version["type"] = type_entry["value"]
        else:
            version["type"] = None

        return version

    def download(self, version="original", **kwargs):
        """Returns the photo file."""
        if version not in self.versions:
            return None

        return self._service.session.get(
            self.versions[version]["url"], stream=True, **kwargs
        )

    def delete(self):
        """Deletes the photo."""
        json_data = (
            '{"operations":[{'
            '"operationType":"update",'
            '"record":{'
            '"recordName":"%s",'
            '"recordType":"%s",'
            '"recordChangeTag":"%s",'
            '"fields":{"isDeleted":{"value":1}'
            "}}}],"
            '"zoneID":{'
            '"zoneName":"PrimarySync"'
            '},"atomic":true}'
            % (
                self._asset_record["recordName"],
                self._asset_record["recordType"],
                self._master_record["recordChangeTag"],
            )
        )

        endpoint = self._service.service_endpoint
        params = urlencode(self._service.params)
        url = f"{endpoint}/records/modify?{params}"

        return self._service.session.post(
            url, data=json_data, headers={CONTENT_TYPE: CONTENT_TYPE_TEXT}
        )

    def __repr__(self):
        return f"<{type(self).__name__}: id={self.id}>"


class PhotoStreamAsset(PhotoAsset):
    """A Shared Stream Photo Asset"""

    def __init__(self, service, master_record, asset_record):
        super().__init__(service, master_record, asset_record)

    @property
    def like_count(self):
        return self._asset_record["pluginFields"]["likeCount"]["value"]

    @property
    def liked(self):
        return bool(self._asset_record["pluginFields"]["likedByCaller"]["value"])