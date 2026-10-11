"""Branch-coverage tests for the CloudKit notes base models (issue #394)."""

# pylint: disable=protected-access

from __future__ import annotations

import pytest

from pyicloud.services.notes.models import _ck_base


def _mode(
    monkeypatch: pytest.MonkeyPatch,
    *,
    value: str | None = None,
    fallback: str | None = None,
    default: str = "forbid",
) -> str:
    """Resolve the extra-mode with a clean environment plus optional overrides."""
    monkeypatch.delenv("PYICLOUD_NOTES_EXTRA", raising=False)
    monkeypatch.delenv("PYICLOUD_EXTRA", raising=False)
    if value is not None:
        monkeypatch.setenv("PYICLOUD_NOTES_EXTRA", value)
    if fallback is not None:
        monkeypatch.setenv("PYICLOUD_EXTRA", fallback)
    return _ck_base._env_extra_mode(default)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("allow", "allow"),
        ("forbid", "forbid"),
        ("ignore", "ignore"),
        ("1", "forbid"),
        ("true", "forbid"),
        ("yes", "forbid"),
        ("on", "forbid"),
        ("strict", "forbid"),
        ("0", "allow"),
        ("false", "allow"),
        ("no", "allow"),
        ("off", "allow"),
        ("lenient", "allow"),
        ("weird", "forbid"),
        ("  ALLOW  ", "allow"),
        ("", "forbid"),
    ],
)
def test_env_extra_mode_from_notes_var(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: str
) -> None:
    """The notes-specific variable wins and accepts the documented spellings."""
    assert _mode(monkeypatch, value=value) == expected


def test_env_extra_mode_falls_back_to_generic_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generic PYICLOUD_EXTRA variable is used when the notes one is unset."""
    assert _mode(monkeypatch, fallback="allow") == "allow"
    assert _mode(monkeypatch, value="", fallback="ignore") == "ignore"
    assert _mode(monkeypatch, value="allow", fallback="ignore") == "allow"


def test_env_extra_mode_uses_custom_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognised value returns the caller-supplied default."""
    assert _mode(monkeypatch, value="nonsense", default="ignore") == "ignore"
    assert _mode(monkeypatch, default="allow") == "allow"


def test_ck_model_uses_resolved_extra_mode() -> None:
    """CKModel exposes the module-level extra mode in its config."""
    assert _ck_base.CKModel.model_config["extra"] == _ck_base._EXTRA
    assert set(_ck_base.__all__) == {"CKModel", "_env_extra_mode"}
