"""Frozen consumer contracts derived from release 2026.10.1."""

from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest

from pyicloud import AppleDevice, PyiCloudService
from pyicloud.base import PyiCloudService as CoreService
from pyicloud.services.findmyiphone import AppleDevice as Device
from pyicloud.services.reminders.models import Reminder, RemindersList
from scripts.check.compatibility import check_manifest, compatible_signature
from tests.fixture_loader import load_fixture


def baseline() -> dict[str, Any]:
    """Load reviewed expectations through the sanctioned fixture loader."""
    return dict(load_fixture("compatibility", "2026.10.1.json"))


def test_released_imports_signatures_and_exceptions() -> None:
    """Facade identity, metadata, call contracts and catch clauses stay stable."""
    check_manifest(baseline())
    assert PyiCloudService is CoreService
    assert AppleDevice is Device


@pytest.mark.parametrize(
    ("expected", "actual", "compatible"),
    [
        ("(a, b=None)", "(b=None, a=None)", False),
        ("(a, b=None)", "(a, b=False)", False),
        ("(a, b=None)", "(a, /, b=None)", False),
        ("(a, b=None)", "(a)", False),
        ("(a)", "(a, *, optional=None)", True),
        ("(a)", "(a, *, required)", False),
        ("(a)", "(a, extra=None)", False),
    ],
)
def test_signature_regressions_are_detected(
    expected: str, actual: str, compatible: bool
) -> None:
    """New optional keyword arguments are additive; shifting positional calls is not."""
    assert compatible_signature(expected, actual) is compatible


def test_missing_supported_symbol_fails() -> None:
    """A removed root export must not be blessed by generating a new baseline."""
    manifest = baseline()
    with patch("pyicloud.PyiCloudService", None), pytest.raises(TypeError):
        check_manifest(manifest)


def test_exception_regression_fails() -> None:
    """A changed catch-clause relationship fails with the affected class name."""
    manifest = deepcopy(baseline())
    manifest["exceptions"]["pyicloud.exceptions.PyiCloudAPIResponseException"] = [
        "builtins.ValueError"
    ]
    with pytest.raises(AssertionError, match="no longer derives"):
        check_manifest(manifest)


def test_representative_return_fields_and_defaults() -> None:
    """Field removals or changed defaults fail; additive fields remain possible."""
    manifest = baseline()
    for model, expected in [
        (RemindersList(id="List/X", title="Synthetic"), manifest["list_shape"]),
        (
            Reminder(id="Reminder/X", list_id="List/X", title="Synthetic"),
            manifest["reminder_shape"],
        ),
    ]:
        actual = model.model_dump(mode="json")
        assert {key: actual[key] for key in expected} == expected
