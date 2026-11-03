"""In-memory synthetic staging has no paths, network, credentials or approvals."""

import base64
from dataclasses import FrozenInstanceError
import hashlib
from typing import Any, cast
from unittest.mock import patch

import pytest

from pyicloud.mcp.authorization import WriteDenied
from pyicloud.mcp.upload_staging import MAX_CHUNK_BYTES, UploadStaging


def digest(data: bytes) -> str:
    """All bytes are synthetic fixture data, not user filesystem contents."""
    return hashlib.sha256(data).hexdigest()


def test_exact_bytes_seal_is_immutable_private_and_single_use() -> None:
    """Chunk ordering, size and independent digest all bind one sealed object."""
    store = UploadStaging()
    data = b"private synthetic bytes"
    token = store.begin("Synthetic.txt", len(data), digest(data))
    assert store.describe(token) == {
        "name": "Synthetic.txt",
        "size": len(data),
        "sha256": digest(data),
        "sealed": False,
    }
    with pytest.raises(WriteDenied, match="upload_not_sealed"):
        store.peek(token)
    assert store.append(token, 0, base64.b64encode(data[:4]).decode()) == 4
    with pytest.raises(WriteDenied, match="incomplete_upload"):
        store.seal(token)
    with pytest.raises(WriteDenied, match="upload_not_sealed"):
        store.take(token)
    store.append(token, 4, base64.b64encode(data[4:]).decode())
    sealed = store.seal(token)
    assert sealed.data == data
    assert sealed.sha256 == digest(data)
    assert store.seal(token) is sealed
    assert "private synthetic" not in repr(sealed)
    with pytest.raises(FrozenInstanceError):
        cast(Any, sealed).data = b"changed"
    with pytest.raises(WriteDenied, match="already_sealed"):
        store.append(token, len(data), "YQ==")
    assert store.take(token) is sealed
    with pytest.raises(WriteDenied, match="expired_or_unknown"):
        store.take(token)


@pytest.mark.parametrize(
    "name,size,sha",
    [
        ("../outside", 1, "0" * 64),
        ("https://example.test", 1, "0" * 64),
        ("x\\file", 1, "0" * 64),
        (".", 1, "0" * 64),
        ("bad\nname", 1, "0" * 64),
        ("x" * 256, 1, "0" * 64),
        ("\ud800", 1, "0" * 64),
        ("x", True, "0" * 64),
        ("x", -1, "0" * 64),
        ("x", 16777217, "0" * 64),
        ("x", 1, "NOT_A_DIGEST"),
    ],
)
def test_declaration_rejects_paths_controls_sizes_and_bad_digest(
    name: str, size: Any, sha: str
) -> None:
    """Invalid declarations cannot reserve memory or identify local resources."""
    with pytest.raises(WriteDenied, match="invalid_upload_declaration"):
        UploadStaging().begin(name, size, sha)


@pytest.mark.parametrize(
    "offset,encoded",
    [
        (1, "YQ=="),
        (True, "YQ=="),
        (0, ""),
        (0, "not base64!"),
        (0, "é"),
        (0, "YQ==\n"),
        (0, "YWJj"),
        (0, "A" * 100000),
    ],
)
def test_invalid_chunk_cannot_change_reserved_bytes(offset: Any, encoded: str) -> None:
    """Rejected chunks leave the exact next offset and reserved bytes intact."""
    store = UploadStaging()
    token = store.begin("x", 1, digest(b"a"))
    with pytest.raises(WriteDenied, match="invalid_upload_chunk"):
        store.append(token, offset, encoded)
    assert store.append(token, 0, "YQ==") == 1
    assert store.seal(token).data == b"a"


def test_mismatch_expiry_reservation_and_cleanup_release_capacity() -> None:
    """Capacity is bounded by declared sizes, with expiry and safe cleanup."""
    store = UploadStaging(max_file_bytes=2, max_total_bytes=2, capacity=1, ttl=1)
    with patch("pyicloud.mcp.upload_staging.time.monotonic", return_value=0):
        token = store.begin("x", 2, digest(b"ab"))
        with pytest.raises(WriteDenied, match="staging_full"):
            store.begin("other", 1, digest(b"a"))
    with patch("pyicloud.mcp.upload_staging.time.monotonic", return_value=2):
        with pytest.raises(WriteDenied, match="expired_or_unknown"):
            store.seal(token)
        wrong = store.begin("wrong", 1, digest(b"b"))
        store.append(wrong, 0, "YQ==")
        with pytest.raises(WriteDenied, match="digest_mismatch"):
            store.seal(wrong)
        empty = store.begin("empty", 0, digest(b""))
        assert store.seal(empty).data == b""
        store.discard(empty)
        remaining = store.begin("x", 1, digest(b"a"))
        store.close()
        with pytest.raises(WriteDenied, match="expired_or_unknown"):
            store.seal(remaining)


def test_aggregate_reserves_declared_sizes_and_chunk_bound() -> None:
    """Incomplete stages cannot overbook aggregate memory or accept giant chunks."""
    store = UploadStaging(max_file_bytes=2, max_total_bytes=2, capacity=2)
    store.begin("first", 2, digest(b"ab"))
    with pytest.raises(WriteDenied, match="staging_full"):
        store.begin("second", 1, digest(b"a"))
    store = UploadStaging()
    large = b"a" * (MAX_CHUNK_BYTES + 1)
    token = store.begin("large", len(large), digest(large))
    with pytest.raises(WriteDenied, match="invalid_upload_chunk"):
        store.append(token, 0, base64.b64encode(large).decode())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_file_bytes": 0},
        {"max_total_bytes": 1},
        {"capacity": 0},
        {"ttl": 0},
        {"capacity": True},
        {"max_file_bytes": 1.5},
    ],
)
def test_invalid_staging_limits(kwargs: dict[str, Any]) -> None:
    """Only strict bounded operator limits can create a staging store."""
    with pytest.raises(ValueError):
        UploadStaging(**kwargs)
