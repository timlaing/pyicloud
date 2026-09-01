"""Tests for the webservice inventory.

The inventory is only worth having if it stays true, so the important test here
scans the library source for resolved webservice keys and fails when the code
and the inventory disagree in either direction.
"""

from __future__ import annotations

from pathlib import Path
import re

from pyicloud.endpoints import (
    WEBSERVICE_KEYS,
    WEBSERVICES,
    services_using,
    webservice,
    webservices_for,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "pyicloud"

# Matches get_webservice_url("key") and any future sibling resolver, so adding a
# tolerant variant does not silently escape this check.
RESOLVER = re.compile(r"""webservice_url\(\s*["']([a-zA-Z0-9_]+)["']\s*\)""")


def _keys_resolved_in_source() -> set[str]:
    """Return every webservice key the library resolves by literal name."""

    found: set[str] = set()
    for source in PACKAGE_ROOT.rglob("*.py"):
        found.update(RESOLVER.findall(source.read_text(encoding="utf-8")))
    return found


def test_inventory_matches_the_keys_the_library_resolves() -> None:
    """The inventory and the source must not drift apart.

    A key resolved but unlisted means the inventory understates our exposure; a
    key listed but never resolved means it is stale. Both are failures.
    """

    resolved = _keys_resolved_in_source()

    assert resolved, "found no webservice lookups; the source scan is broken"

    unlisted = resolved - WEBSERVICE_KEYS
    assert not unlisted, (
        f"resolved in code but missing from the inventory: {sorted(unlisted)}. "
        "Add them to pyicloud/endpoints.py."
    )

    stale = WEBSERVICE_KEYS - resolved
    assert not stale, (
        f"listed in the inventory but never resolved: {sorted(stale)}. "
        "Remove them from pyicloud/endpoints.py."
    )


def test_inventory_entries_are_well_formed() -> None:
    """Every entry names at least one service and describes itself."""

    for entry in WEBSERVICES:
        assert entry.key, "an entry has no key"
        assert entry.powers, f"{entry.key} names no service that depends on it"
        assert entry.description.strip(), f"{entry.key} has no description"


def test_inventory_keys_are_unique_and_sorted() -> None:
    """Keys are unique, and kept in order so additions produce clean diffs."""

    keys = [entry.key for entry in WEBSERVICES]
    assert len(keys) == len(set(keys)), "duplicate keys in the inventory"
    assert keys == sorted(keys), "inventory is not in key order"


def test_lookup_helpers_agree_with_the_inventory() -> None:
    """The helpers are consistent with the data they read."""

    for entry in WEBSERVICES:
        assert webservice(entry.key) is entry
        assert services_using(entry.key) == entry.powers
        for service in entry.powers:
            assert entry.key in webservices_for(service)

    assert webservice("not-a-real-webservice") is None
    assert not services_using("not-a-real-webservice")
    assert not webservices_for("not-a-real-service")


def test_cloudkit_is_recorded_as_the_shared_dependency() -> None:
    """`ckdatabasews` backs several services, which is worth asserting.

    It is the widest blast radius in the inventory, so a change that narrowed it
    by accident should fail rather than pass quietly.
    """

    entry = webservice("ckdatabasews")
    assert entry is not None
    assert set(entry.powers) >= {"photos", "reminders", "notes"}
    assert len(entry.powers) > 1
