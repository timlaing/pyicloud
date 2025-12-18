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
        # Copy to list first to avoid dict mutation during iteration
        try:
            cookies_snapshot = list(self)
        except RuntimeError:
            cookies_snapshot = []
        cookies_to_clear: list[tuple[str, str, str]] = [
            (cookie.domain, cookie.path, cookie.name)
            for cookie in cookies_snapshot
            if cookie.name == _FMIP_AUTH_COOKIE_NAME
        ]
        for domain, path, name in cookies_to_clear:
            try:
                self.clear(domain=domain, path=path, name=name)
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
        # Copy cookies to avoid "dictionary changed size during iteration"
        # when concurrent HTTP responses modify the cookie jar
        try:
            cookies_snapshot = list(self)
            # Create temp jar with snapshot for thread-safe save
            from http.cookiejar import LWPCookieJar as TempJar

            temp_jar = TempJar(filename=resolved)
            for cookie in cookies_snapshot:
                temp_jar.set_cookie(cookie)
            temp_jar.save(
                filename=resolved,
                ignore_discard=ignore_discard,
                ignore_expires=ignore_expires,
            )
        except RuntimeError:
            # If we still hit a race, silently skip this save
            pass
