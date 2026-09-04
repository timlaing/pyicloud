"""Tests for the read-only webservice diagnostics.

The value of this module is entirely in how it classifies an imperfect map, so
the cases below are the shapes Apple has actually been observed to send, not
invented ones.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, PropertyMock

import pytest

from pyicloud.diagnostics import (
    PROBED_SERVICES,
    SERVICE_PROBES,
    ProbeStatus,
    WebserviceStatus,
    diagnose_webservices,
    environment,
    installed_version,
    probe_failures,
    probe_services,
    services_at_risk,
    webservice_problems,
)
from pyicloud.endpoints import WEBSERVICES
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
    PyiCloudServiceUnavailable,
)


def _healthy_map() -> dict[str, Any]:
    """Return a map advertising every key the library needs.

    Derived from the inventory rather than hard-coded, because "complete" is
    defined by the inventory; the failure cases below name keys explicitly.
    """

    return {
        entry.key: {
            "url": f"https://p31-{entry.key}.icloud.com:443",
            "status": "active",
        }
        for entry in WEBSERVICES
    }


def _finding(findings: tuple[Any, ...], key: str) -> Any:
    """Return the single finding for a key."""

    matches = [finding for finding in findings if finding.key == key]
    assert len(matches) == 1, f"expected exactly one finding for {key}"
    return matches[0]


def test_a_complete_map_reports_no_problems() -> None:
    """A healthy account produces one OK finding per inventory key."""

    findings = diagnose_webservices(_healthy_map())

    assert len(findings) == len(WEBSERVICES)
    assert all(finding.status is WebserviceStatus.OK for finding in findings)
    assert not webservice_problems(findings)
    assert not services_at_risk(findings)


def test_a_withdrawn_key_names_the_services_it_takes_down() -> None:
    """A missing key is a problem, and says what stops working."""

    advertised = _healthy_map()
    del advertised["ckdatabasews"]

    findings = diagnose_webservices(advertised)
    finding = _finding(findings, "ckdatabasews")

    assert finding.status is WebserviceStatus.MISSING
    assert finding.is_problem
    assert finding.url is None
    assert "photos" in finding.detail
    assert webservice_problems(findings) == (finding,)
    assert services_at_risk(findings) == ("invites", "notes", "photos", "reminders")


def test_a_needed_key_without_a_url_is_a_problem() -> None:
    """An entry with no url would raise rather than report unavailability."""

    advertised = _healthy_map()
    advertised["findme"] = {}

    finding = _finding(diagnose_webservices(advertised), "findme")

    assert finding.status is WebserviceStatus.MALFORMED
    assert finding.is_problem
    assert services_at_risk(diagnose_webservices(advertised)) == ("devices",)


def test_an_unused_key_without_a_url_is_reported_but_not_a_problem() -> None:
    """The `schoolwork: {}` shape a live account actually returns.

    pyicloud never resolves `schoolwork`, so an empty entry for it must not
    condemn the account. It is still worth reporting: Apple advertising a key
    broken is a different upstream state from not advertising it at all.
    """

    advertised = _healthy_map()
    advertised["schoolwork"] = {}

    finding = _finding(diagnose_webservices(advertised), "schoolwork")

    assert finding.status is WebserviceStatus.MALFORMED
    assert not finding.is_problem
    assert not finding.needed
    assert "cannot be resolved" in finding.detail
    assert not webservice_problems(diagnose_webservices(advertised))


def test_an_advertised_null_is_malformed_rather_than_missing() -> None:
    """`{"findme": null}` is an entry with a bad shape, not an absent key.

    Both are problems, so the exit code is the same either way -- but saying
    "Apple is not advertising this key" when Apple did advertise it points the
    reader at the wrong upstream event.
    """

    advertised = _healthy_map()
    advertised["findme"] = None

    findings = diagnose_webservices(advertised)
    finding = _finding(findings, "findme")

    assert finding.status is WebserviceStatus.MALFORMED
    assert finding.is_problem
    assert "not advertising" not in finding.detail

    # An genuinely absent key still reports as missing.
    del advertised["calendar"]
    absent = _finding(diagnose_webservices(advertised), "calendar")
    assert absent.status is WebserviceStatus.MISSING
    assert "not advertising" in absent.detail


def test_keys_the_library_does_not_use_are_context_not_complaints() -> None:
    """Extra advertised services are reported last and never fail the run.

    `mail` is one of roughly twenty keys a live account advertises that this
    library does not wrap.
    """

    advertised = _healthy_map()
    advertised["mail"] = {"url": "https://p49-mailws.icloud.com:443"}

    findings = diagnose_webservices(advertised)
    finding = _finding(findings, "mail")

    assert finding.status is WebserviceStatus.UNUSED
    assert not finding.is_problem
    assert finding.powers == ()
    assert findings[-1] is finding
    assert not webservice_problems(findings)


def test_an_inactive_host_is_usable_but_called_out() -> None:
    """Apple's own status field is surfaced when it is not `active`."""

    advertised = _healthy_map()
    advertised["calendar"]["status"] = "inactive"

    finding = _finding(diagnose_webservices(advertised), "calendar")

    assert finding.status is WebserviceStatus.OK
    assert not finding.is_problem
    assert "inactive" in finding.detail


@pytest.mark.parametrize("advertised", [None, {}])
def test_no_map_at_all_reports_every_key_missing(advertised: Any) -> None:
    """An absent map means nothing the library needs is available."""

    findings = diagnose_webservices(advertised)

    assert len(findings) == len(WEBSERVICES)
    assert all(finding.status is WebserviceStatus.MISSING for finding in findings)
    assert len(webservice_problems(findings)) == len(WEBSERVICES)


@pytest.mark.parametrize("entry", ["https://example.com", 42, [], None])
def test_an_entry_of_the_wrong_shape_is_treated_as_unusable(entry: Any) -> None:
    """Apple sending something unexpected must not raise out of a diagnostic."""

    advertised = _healthy_map()
    advertised["contacts"] = entry

    finding = _finding(diagnose_webservices(advertised), "contacts")

    assert finding.is_problem
    assert finding.url is None


def test_a_blank_url_does_not_count_as_advertised() -> None:
    """An empty or whitespace url is as unusable as a missing one."""

    advertised = _healthy_map()
    advertised["docws"] = {"url": "   "}

    finding = _finding(diagnose_webservices(advertised), "docws")

    assert finding.status is WebserviceStatus.MALFORMED
    assert finding.is_problem


def test_environment_describes_the_local_install() -> None:
    """The environment block has to stand alone when pasted into an issue."""

    report = environment()

    assert set(report) == {"pyicloud", "python", "platform"}
    assert report["pyicloud"] == installed_version()
    assert all(value for value in report.values())


def _probe_api() -> MagicMock:
    """Return an API whose every probed service answers."""

    api = MagicMock()
    api.notes.sync_cursor.return_value = "cursor"
    api.reminders.sync_cursor.return_value = "cursor"
    api.invites.events.return_value = []
    api.calendar.get_calendars.return_value = []
    api.hidemyemail.__len__.return_value = 0
    return api


def test_every_service_in_the_inventory_has_a_probe() -> None:
    """The probe table and the inventory must not drift apart.

    A service named in the inventory but never probed is a silent gap: the run
    reports "each one answered" while quietly skipping it. A probe for a
    service no longer in the inventory is dead code.
    """

    named_by_inventory = {service for entry in WEBSERVICES for service in entry.powers}

    not_probed = named_by_inventory - PROBED_SERVICES
    assert not not_probed, (
        f"named in pyicloud/endpoints.py but never probed: {sorted(not_probed)}. "
        "Add a probe to SERVICE_PROBES or the run will claim to have checked it."
    )

    orphaned = PROBED_SERVICES - named_by_inventory
    assert not orphaned, (
        f"probed but not named in pyicloud/endpoints.py: {sorted(orphaned)}."
    )


def test_every_probe_describes_what_it_does() -> None:
    """A probe makes a real request, so it has to say which one."""

    for probe in SERVICE_PROBES:
        assert probe.describe.strip(), f"{probe.service} does not describe its probe"
        assert callable(probe.call)


def test_probes_report_each_service_answering() -> None:
    """The happy path returns one OK result per probed service."""

    api = _probe_api()

    results = probe_services(api)

    assert len(results) == len(SERVICE_PROBES)
    assert all(result.status is ProbeStatus.OK for result in results)
    assert not probe_failures(results)
    # A MagicMock answers anything, so the happy path alone cannot tell which
    # read each probe chose. These pin the two that matter: listing reminders
    # took 28s against a real account, against 0.3s for the sync cursor.
    api.reminders.sync_cursor.assert_called_once()
    api.notes.sync_cursor.assert_called_once()
    api.reminders.lists.assert_not_called()
    api.notes.folders.assert_not_called()


def test_the_find_my_probe_does_not_ask_devices_to_report_location() -> None:
    """Counting or iterating Find My devices pings the user's hardware.

    A diagnostic must not cause that as a side effect, so the probe calls
    refresh(locate=False) explicitly rather than going through the manager's
    container protocol.
    """

    api = _probe_api()

    probe_services(api)

    api.devices.refresh.assert_called_once_with(locate=False)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PyiCloudServiceUnavailable("Account migrated"), ProbeStatus.UNAVAILABLE),
        (PyiCloudFailedLoginException("No password set"), ProbeStatus.AUTH),
        (
            PyiCloudAuthRequiredException("user@example.com", MagicMock()),
            ProbeStatus.AUTH,
        ),
        (PyiCloudAPIResponseException("Gone", 410), ProbeStatus.GONE),
        (PyiCloudAPIResponseException("Server error", 500), ProbeStatus.ERROR),
        (ValueError("something unexpected"), ProbeStatus.ERROR),
    ],
)
def test_a_failing_probe_is_classified(error: Exception, expected: ProbeStatus) -> None:
    """Each failure shape maps onto a verdict the user can act on."""

    api = _probe_api()
    type(api).account = PropertyMock(side_effect=error)

    result = next(r for r in probe_services(api) if r.service == "account")

    assert result.status is expected
    assert result.detail


def test_account_state_and_auth_are_reported_but_do_not_fail_the_run() -> None:
    """A migrated service or an expired session is not a pyicloud defect.

    Both are worth showing -- they answer the user's question -- but neither
    should make the command report a problem with the library.
    """

    api = _probe_api()
    type(api).files = PropertyMock(
        side_effect=PyiCloudServiceUnavailable("Account migrated")
    )

    results = probe_services(api)
    files = next(r for r in results if r.service == "files")

    assert files.status is ProbeStatus.UNAVAILABLE
    assert not files.is_failure
    assert not probe_failures(results)


def test_a_withdrawn_endpoint_behind_a_healthy_host_fails_the_run() -> None:
    """The case the service map cannot see, which is why probing exists."""

    api = _probe_api()
    type(api).photos = PropertyMock(
        side_effect=PyiCloudAPIResponseException("Gone", 410)
    )

    results = probe_services(api)
    photos = next(r for r in results if r.service == "photos")

    assert photos.status is ProbeStatus.GONE
    assert photos.is_failure
    assert probe_failures(results) == (photos,)


def test_probes_are_skipped_when_the_key_is_already_known_bad() -> None:
    """Calling a service whose host is missing would only restate the map."""

    advertised = _healthy_map()
    del advertised["ckdatabasews"]
    findings = diagnose_webservices(advertised)

    api = _probe_api()
    results = probe_services(api, findings)
    by_service = {result.service: result for result in results}

    for service in ("photos", "reminders", "notes", "invites"):
        assert by_service[service].status is ProbeStatus.SKIPPED
        assert by_service[service].elapsed_ms == 0
    # A service whose key is fine is still probed.
    assert by_service["calendar"].status is ProbeStatus.OK
    api.photos.assert_not_called()
