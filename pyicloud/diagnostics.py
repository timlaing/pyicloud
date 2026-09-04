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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
import platform
import sys
import time
from typing import TYPE_CHECKING, Any

from pyicloud.endpoints import WEBSERVICES, services_using
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
    PyiCloudServiceUnavailable,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from pyicloud.base import PyiCloudService

ACTIVE_STATUS = "active"
UNKNOWN_VERSION = "unknown"

# Apple answers a withdrawn endpoint with 410 rather than 404, which makes it an
# unambiguous signal that the library needs updating. Kept local because #334
# adds the same constant to pyicloud.exceptions; this can defer to it once that
# merges, and until then the check works on `main` unchanged.
GONE_STATUS = 410


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


class ProbeStatus(str, Enum):
    """The verdict for one service after actually calling it."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    AUTH = "auth"
    GONE = "gone"
    ERROR = "error"
    SKIPPED = "skipped"


#: Probe outcomes that mean the service did not answer as expected.
PROBE_FAILURES: frozenset[ProbeStatus] = frozenset({
    ProbeStatus.GONE,
    ProbeStatus.ERROR,
})


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What one service did when the cheapest available read was made against it."""

    service: str
    status: ProbeStatus
    detail: str
    elapsed_ms: int

    @property
    def is_failure(self) -> bool:
        """Return whether this outcome means something is actually broken.

        ``UNAVAILABLE`` and ``AUTH`` are deliberately excluded. A service Apple
        reports as unavailable for this account -- a migrated Ubiquity library,
        say -- is account state, and a service asking for re-authentication is
        the user's session, not a defect. Both are worth showing and neither
        should fail the run.
        """

        return self.status in PROBE_FAILURES


@dataclass(frozen=True, slots=True)
class ServiceProbe:
    """The cheapest read that proves one service answers.

    ``describe`` is shown to the user, because a probe makes a real request and
    they are entitled to know which one before opting in.
    """

    service: str
    describe: str
    call: Callable[[PyiCloudService], object]


def _probe_devices(api: PyiCloudService) -> object:
    """Refresh Find My without asking the devices to report their location.

    Iterating or counting the manager refreshes with ``locate=True``, which
    pings the user's hardware. A diagnostic must not do that.
    """

    api.devices.refresh(locate=False)
    return "refreshed"


#: One read per service property named in the inventory. Each is a GET or the
#: service's own documented refresh; none of them writes.
SERVICE_PROBES: tuple[ServiceProbe, ...] = (
    ServiceProbe("account", "reads storage usage", lambda api: api.account.storage),
    ServiceProbe(
        "calendar", "lists calendars", lambda api: api.calendar.get_calendars()
    ),
    ServiceProbe(
        "contacts", "refreshes the contact list", lambda api: api.contacts.all
    ),
    ServiceProbe("devices", "refreshes Find My without locating", _probe_devices),
    ServiceProbe("drive", "reads the Drive root", lambda api: api.drive.root.name),
    ServiceProbe("files", "reads the Ubiquity root", lambda api: api.files.root),
    ServiceProbe(
        "hidemyemail",
        "counts Hide My Email addresses",
        lambda api: len(api.hidemyemail),
    ),
    ServiceProbe("invites", "lists invite events", lambda api: api.invites.events()),
    # sync_cursor() rather than a listing for the two zone-backed services: it
    # is a single token fetch and proves the same reachability. Listing
    # reminders took 28s against a real account, which is not a diagnostic.
    ServiceProbe(
        "notes", "reads the notes sync cursor", lambda api: api.notes.sync_cursor()
    ),
    ServiceProbe(
        "photos", "resolves photo libraries", lambda api: api.photos.libraries
    ),
    ServiceProbe(
        "reminders",
        "reads the reminders sync cursor",
        lambda api: api.reminders.sync_cursor(),
    ),
)

PROBED_SERVICES: frozenset[str] = frozenset(probe.service for probe in SERVICE_PROBES)


def _classify(error: Exception) -> tuple[ProbeStatus, str]:
    """Map an exception from a probe onto a verdict the user can act on."""

    if isinstance(error, PyiCloudServiceUnavailable):
        return ProbeStatus.UNAVAILABLE, f"Apple reports this unavailable: {error}"
    if isinstance(error, PyiCloudFailedLoginException | PyiCloudAuthRequiredException):
        return ProbeStatus.AUTH, "Needs re-authentication; run `icloud auth login`."
    if isinstance(error, PyiCloudAPIResponseException) and error.code == GONE_STATUS:
        return (
            ProbeStatus.GONE,
            "Apple no longer serves this endpoint (410); pyicloud needs updating.",
        )
    return ProbeStatus.ERROR, f"{type(error).__name__}: {error}"


def _blocked_services(findings: tuple[WebserviceFinding, ...]) -> frozenset[str]:
    """Return services whose webservice key is already known to be unusable.

    Calling these would only restate what the service map already said, in a
    slower and less specific way.
    """

    blocked: set[str] = set()
    for finding in findings:
        if finding.is_problem:
            blocked.update(finding.powers)
    return frozenset(blocked)


def probe_services(
    api: PyiCloudService,
    findings: tuple[WebserviceFinding, ...] = (),
) -> tuple[ProbeResult, ...]:
    """Call each service's cheapest read and report what happened.

    This is the layer the service map cannot cover: a host can be advertised
    and reachable while the endpoint behind it has been withdrawn. It makes one
    real request per service, which is why the CLI keeps it behind a flag.
    """

    blocked = _blocked_services(findings)
    results: list[ProbeResult] = []

    for probe in SERVICE_PROBES:
        if probe.service in blocked:
            results.append(
                ProbeResult(
                    service=probe.service,
                    status=ProbeStatus.SKIPPED,
                    detail="Its webservice key is already reported above.",
                    elapsed_ms=0,
                )
            )
            continue

        started = time.monotonic()
        try:
            probe.call(api)
        except Exception as error:  # noqa: BLE001 - a probe must survive anything
            status, detail = _classify(error)
        else:
            status, detail = ProbeStatus.OK, ""
        results.append(
            ProbeResult(
                service=probe.service,
                status=status,
                detail=detail,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        )

    return tuple(results)


def probe_failures(results: tuple[ProbeResult, ...]) -> tuple[ProbeResult, ...]:
    """Return only the probe outcomes that mean something is broken."""

    return tuple(result for result in results if result.is_failure)
