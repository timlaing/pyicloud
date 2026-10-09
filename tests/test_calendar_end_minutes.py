"""Synthetic wall-clock examples pin Calendar tuple arithmetic."""

# pylint: disable=protected-access

from datetime import datetime
from typing import Any

import pytest

from pyicloud.services.calendar import AppleDateFormat


@pytest.mark.parametrize(
    "hour,minute,remaining",
    [(0, 0, 0), (0, 1, 1439), (9, 30, 870), (12, 0, 720), (23, 59, 1)],
)
def test_end_minutes_count_down_to_midnight(
    hour: Any, minute: Any, remaining: Any
) -> None:
    """End minutes count down to midnight."""
    value = AppleDateFormat.from_datetime(
        datetime(2025, 1, 2, hour, minute), is_start=False
    )
    assert value.minutes_from_midnight == remaining
    assert value.to_list()[6] == remaining


@pytest.mark.parametrize("hour,minute", [(0, 0), (9, 30), (23, 59)])
def test_start_minutes_keep_the_elapsed_count(hour: Any, minute: Any) -> None:
    """Start minutes keep the elapsed count."""
    value = AppleDateFormat.from_datetime(datetime(2025, 1, 2, hour, minute))
    assert value.minutes_from_midnight == hour * 60 + minute
