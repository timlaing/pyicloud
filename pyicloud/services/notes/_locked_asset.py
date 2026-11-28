"""Isolated, bounded download of a locked Notes asset."""

from urllib.parse import urlsplit

from requests import Request, RequestException, Session

from pyicloud.common.cloudkit.models import CKAssetToken

from ._locked_note import MAX_LOCKED_ASSET_BYTES
from .exceptions import NotesError

_ASSET_ERROR = "Could not download the locked Notes asset safely."


def download_locked_asset(asset: CKAssetToken) -> bytes:
    """Fetch the exact advertised ciphertext without account credentials."""
    url = asset.downloadURL or ""
    size = asset.size
    try:
        parsed = urlsplit(url)
        valid_host = parsed.netloc.lower() == "cvws.icloud-content.com"
    except ValueError:
        raise NotesError(_ASSET_ERROR) from None
    if (
        size is None
        or not 1 <= size <= MAX_LOCKED_ASSET_BYTES
        or parsed.scheme != "https"
        or not valid_host
        or parsed.fragment
        or not parsed.path.startswith("/B/")
        or any(ord(char) <= 32 or char == "\\" for char in url)
    ):
        raise NotesError(_ASSET_ERROR)

    try:
        with Session() as session:
            session.trust_env = False
            request = session.prepare_request(
                Request("GET", url, headers={"Accept-Encoding": "identity"})
            )
            with session.send(
                request,
                stream=True,
                allow_redirects=False,
                timeout=(10, 30),
                verify=True,
                proxies={},
                cert=None,
            ) as response:
                if (
                    response.status_code != 200
                    or response.headers.get("Content-Encoding", "identity")
                    != "identity"
                ):
                    raise NotesError(_ASSET_ERROR)
                data = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if len(data) + len(chunk) > size:
                        raise NotesError(_ASSET_ERROR)
                    data.extend(chunk)
                if len(data) != size:
                    raise NotesError(_ASSET_ERROR)
                return bytes(data)
    except (RequestException, OSError):
        raise NotesError(_ASSET_ERROR) from None
