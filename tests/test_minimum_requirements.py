"""The minimum-dependency job cannot silently install newer direct dependencies."""

import pytest

from scripts.check.minimum import check_minimum, lower_bounds


def test_normalized_lower_bounds() -> None:
    """Comments, upper bounds and project-name punctuation do not change minima."""
    assert lower_bounds([
        "# bounds",
        "",
        "pydantic>=2.13.4,<3",
        "keyrings.alt==5.0.2",
    ]) == {
        "pydantic": "2.13.4",
        "keyrings-alt": "5.0.2",
    }
    check_minimum(["pydantic>=2.13.4,<3"], ["# exact", "", "pydantic==2.13.4"])


@pytest.mark.parametrize("lines", [["pydantic~=2.13"], ["pydantic>=2", "pydantic==2"]])
def test_unsupported_or_duplicate_requirements_fail(lines: list[str]) -> None:
    """Unsupported constraints fail explicitly rather than choosing false minima."""
    with pytest.raises(ValueError):
        lower_bounds(lines)


@pytest.mark.parametrize(
    "pins",
    [
        [],
        ["pydantic==2.14"],
        ["pydantic>=2.13.4"],
        ["pydantic==2.13.4,<3"],
        ["pydantic==2.13.4", "extra==1"],
    ],
)
def test_wrong_or_widened_minimum_pins_fail(pins: list[str]) -> None:
    """Missing, extra, newer or inexact constraints cannot pass the gate."""
    with pytest.raises(ValueError, match="Minimum"):
        check_minimum(["pydantic>=2.13.4,<3"], pins)
