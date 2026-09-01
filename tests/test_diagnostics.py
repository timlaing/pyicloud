"""Tests for the read-only webservice diagnostics.

The value of this module is entirely in how it classifies an imperfect map, so
the cases below are the shapes Apple has actually been observed to send, not
invented ones.
"""

from __future__ import annotations

from typing import Any

import pytest

from pyicloud.diagnostics import (
    WebserviceStatus,
    diagnose_webservices,
    environment,
    installed_version,
    services_at_risk,
    webservice_problems,
)
from pyicloud.endpoints import WEBSERVICES


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
    condemn the account -- but `get_webservice_url()` is public and would raise
    KeyError on it, so it is still worth seeing.
    """

    advertised = _healthy_map()
    advertised["schoolwork"] = {}

    finding = _finding(diagnose_webservices(advertised), "schoolwork")

    assert finding.status is WebserviceStatus.MALFORMED
    assert not finding.is_problem
    assert not finding.needed
    assert "KeyError" in finding.detail
    assert not webservice_problems(diagnose_webservices(advertised))


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
