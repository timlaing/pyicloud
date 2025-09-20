"""Cookie jar with persistence support."""

from http.cookiejar import LWPCookieJar
from typing import Optional

from requests.cookies import RequestsCookieJar

_FMIP_AUTH_COOKIE_NAME: str = "X-APPLE-WEBAUTH-FMIP"


class PyiCloudCookieJar(RequestsCookieJar, LWPCookieJar):
    """Mix the Requests CookieJar with the LWPCookieJar to allow persistance"""

    def __init__(self, filename: Optional[str] = None) -> None:
        """Initialise both bases; do not pass filename positionally to RequestsCookieJar."""
        RequestsCookieJar.__init__(self)
        LWPCookieJar.__init__(self, filename=filename)

    def _resolve_filename(self, filename: Optional[str]) -> Optional[str]:
        resolved: Optional[str] = filename or getattr(self, "filename", None)
        if not resolved:
            return  # No-op if no filename is bound
        return resolved

    def load(
        self,
        filename: Optional[str] = None,
        ignore_discard: bool = True,
        ignore_expires: bool = False,
    ) -> None:
        """Load cookies from file."""
        resolved: Optional[str] = self._resolve_filename(filename)
        if not resolved:
            return  # No-op if no filename is bound
        super().load(
            filename=resolved,
            ignore_discard=ignore_discard,
            ignore_expires=ignore_expires,
        )
        # Clear any FMIP cookie regardless of domain/path to avoid stale auth.
        for cookie in self:
            if cookie.name == _FMIP_AUTH_COOKIE_NAME:
                try:
                    self.clear(domain=cookie.domain, path=cookie.path, name=cookie.name)
                except KeyError:
                    pass

    def save(
        self,
        filename: Optional[str] = None,
        ignore_discard: bool = True,
        ignore_expires: bool = False,
    ) -> None:
        """Save cookies to file."""
        resolved: Optional[str] = self._resolve_filename(filename)
        if not resolved:
            return  # No-op if no filename is bound
        super().save(
            filename=resolved,
            ignore_discard=ignore_discard,
            ignore_expires=ignore_expires,
        )
