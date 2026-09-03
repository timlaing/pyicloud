"""Read-only checks against what Apple is advertising to this account.

pyicloud depends on a ``webservices`` map that Apple returns at login, and that
map changes without notice: hosts move between partitions, keys stop being
advertised, and entries occasionally arrive without a usable URL. When one of
those things happens the failure surfaces deep inside a service call, a long
way from its cause -- which is how a withdrawn Photos upload endpoint came to
be diagnosed by ruling out nine unrelated hypotheses first.

This module compares the map Apple actually sent against the inventory in
:mod:`pyicloud.endpoints`, so "is this my session or Apple's side" can be
answered by looking instead of by elimination. It performs no I/O of its own:
it reads a map that authentication has already fetched.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
import platform
import sys
from typing import Any

from pyicloud.endpoints import WEBSERVICES, services_using

ACTIVE_STATUS = "active"
UNKNOWN_VERSION = "unknown"


def installed_version() -> str:
    """Return the installed pyicloud version, or a marker when it cannot be read."""

    try:
        return package_version("pyicloud")
    except PackageNotFoundError:
        return UNKNOWN_VERSION


def environment() -> dict[str, str]:
    """Describe the local install, so a report stands on its own in a bug thread."""

    return {
        "pyicloud": installed_version(),
        "python": platform.python_version(),
        "platform": f"{sys.platform} ({platform.machine()})",
    }


class WebserviceStatus(str, Enum):
    """The verdict for one key, from pyicloud's point of view."""

    OK = "ok"
    MISSING = "missing"
    MALFORMED = "malformed"
    UNUSED = "unused"


@dataclass(frozen=True, slots=True)
class WebserviceFinding:
    """What one webservice key looks like right now.

    ``powers`` names the service properties that depend on the key, so a
    problem reads as "these stop working" rather than as a bare key name, and
    an empty ``powers`` means this library does not wrap the key at all.
    """

    key: str
    status: WebserviceStatus
    powers: tuple[str, ...]
    url: str | None
    detail: str

    @property
    def needed(self) -> bool:
        """Return whether pyicloud itself resolves this key."""

        return bool(self.powers)

    @property
    def is_problem(self) -> bool:
        """Return whether this finding breaks something pyicloud does.

        A malformed entry for a key the library never resolves is reported but
        is not a problem here: ``get_webservice_url()`` is public and a caller
        may still ask for it, so it is worth seeing, but Apple sending an empty
        entry for a service pyicloud does not wrap should not condemn the whole
        account.
        """

        if self.status is WebserviceStatus.MISSING:
            return True
        return self.status is WebserviceStatus.MALFORMED and self.needed


def _entry_url(entry: Any) -> str | None:
    """Return the URL Apple advertised for one entry, if it advertised a usable one."""

    if not isinstance(entry, Mapping):
        return None
    url = entry.get("url")
    return url if isinstance(url, str) and url.strip() else None


def _entry_status(entry: Any) -> str | None:
    """Return Apple's own status field for one entry, when it sends one."""

    if not isinstance(entry, Mapping):
        return None
    status = entry.get("status")
    return status if isinstance(status, str) and status.strip() else None


def _describe_known(
    key: str, entry: Any, powers: tuple[str, ...], *, advertised: bool
) -> WebserviceFinding:
    """Build the finding for a key the library depends on.

    ``advertised`` is passed rather than inferred from ``entry``, because a key
    Apple sends as ``null`` is an entry with an unusable shape, not an absent
    one, and reporting it as "not advertising this key" would be false.
    """

    affected = ", ".join(powers)

    if not advertised:
        return WebserviceFinding(
            key=key,
            status=WebserviceStatus.MISSING,
            powers=powers,
            url=None,
            detail=f"Apple is not advertising this key; {affected} will not work.",
        )

    url = _entry_url(entry)
    if url is None:
        # Deliberately says what the condition means rather than which
        # exception it produces: Apple advertising a key broken is a different
        # upstream event from not advertising it, whatever the library then
        # raises.
        return WebserviceFinding(
            key=key,
            status=WebserviceStatus.MALFORMED,
            powers=powers,
            url=None,
            detail=(
                f"Advertised without a usable url, so it cannot be resolved "
                f"and {affected} will not work."
            ),
        )

    apple_status = _entry_status(entry)
    detail = ""
    if apple_status is not None and apple_status != ACTIVE_STATUS:
        detail = f"Apple reports this host as {apple_status!r} rather than active."

    return WebserviceFinding(
        key=key,
        status=WebserviceStatus.OK,
        powers=powers,
        url=url,
        detail=detail,
    )


def _describe_unused(key: str, entry: Any) -> WebserviceFinding:
    """Build the finding for a key Apple advertises that the library does not wrap."""

    url = _entry_url(entry)
    if url is None:
        return WebserviceFinding(
            key=key,
            status=WebserviceStatus.MALFORMED,
            powers=(),
            url=None,
            detail=(
                "Advertised without a usable url, so it cannot be resolved. "
                "pyicloud does not use this key, so nothing here depends on it."
            ),
        )
    # No detail: the status already says everything there is to say, and a
    # live account advertises enough of these to bury the real notes.
    return WebserviceFinding(
        key=key,
        status=WebserviceStatus.UNUSED,
        powers=(),
        url=url,
        detail="",
    )


def diagnose_webservices(
    advertised: Mapping[str, Any] | None,
) -> tuple[WebserviceFinding, ...]:
    """Compare Apple's advertised map against the keys this library needs.

    Every inventory key produces a finding, so the output is a full account of
    what pyicloud depends on rather than only a list of complaints. Keys Apple
    advertises that the library does not wrap are reported last as ``UNUSED``:
    they are context for a bug report, not problems.
    """

    entries: Mapping[str, Any] = advertised or {}

    findings: list[WebserviceFinding] = [
        _describe_known(
            entry.key,
            entries.get(entry.key),
            entry.powers,
            advertised=entry.key in entries,
        )
        for entry in WEBSERVICES
    ]

    known = {entry.key for entry in WEBSERVICES}
    findings.extend(
        _describe_unused(key, entries.get(key)) for key in sorted(set(entries) - known)
    )

    return tuple(findings)


def webservice_problems(
    findings: tuple[WebserviceFinding, ...],
) -> tuple[WebserviceFinding, ...]:
    """Return only the findings that need the user to do something."""

    return tuple(finding for finding in findings if finding.is_problem)


def services_at_risk(
    findings: tuple[WebserviceFinding, ...],
) -> tuple[str, ...]:
    """Return the service properties that a current problem would break."""

    at_risk: set[str] = set()
    for finding in webservice_problems(findings):
        at_risk.update(services_using(finding.key))
    return tuple(sorted(at_risk))
