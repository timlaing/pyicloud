"""Observed album identity metadata is read-only and never triggers transport."""

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from pyicloud.services.photos.constants import (
    DirectionEnum,
    ListTypeEnum,
    ObjectTypeEnum,
)
from pyicloud.services.photos.service import (
    PhotoAlbum,
    PhotoAlbumRecordState,
)


def make_album(
    library: Any,
    name: str = "Before",
    tag: str | None = "R1",
    parent: str | None = None,
) -> PhotoAlbum:
    """Construct a synthetic core album without any integration dependencies."""
    return PhotoAlbum(
        library,
        name=name,
        record_id="A",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.ASCENDING,
        zone_id={"zoneName": "Z"},
        record=PhotoAlbumRecordState(parent_id=parent, record_change_tag=tag),
    )


@pytest.mark.parametrize("tag", [None, "R1"])
@pytest.mark.parametrize("parent", [None, "PARENT"])
def test_album_identity_accessors_are_detached_and_read_only(
    tag: str | None, parent: str | None
) -> None:
    """Missing revisions stay unknown; returned zones cannot redirect mutations."""
    library = MagicMock()
    session = MagicMock()
    library.service.session = session
    library.service.params = {}
    album = PhotoAlbum(
        library,
        name="Synthetic",
        record_id="ALBUM",
        obj_type=ObjectTypeEnum.CONTAINER,
        list_type=ListTypeEnum.DEFAULT,
        direction=DirectionEnum.ASCENDING,
        zone_id={"zoneName": "Z"},
        record=PhotoAlbumRecordState(parent_id=parent, record_change_tag=tag),
    )
    assert album.record_change_tag == tag
    assert album.parent_id == parent
    assert album.object_type == ObjectTypeEnum.CONTAINER
    original = album.zone_id
    original["zoneName"] = "UNAPPROVED"
    assert album.zone_id == {"zoneName": "Z"}
    assert original is not album.zone_id
    with pytest.raises(AttributeError):
        cast(Any, album).record_change_tag = "UNAPPROVED"
    with pytest.raises(AttributeError):
        cast(Any, album).zone_id = {"zoneName": "UNAPPROVED"}
    session.get.assert_not_called()
    session.post.assert_not_called()
