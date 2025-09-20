"""
Tests for the utils module.
"""

import pytest

from pyicloud.utils import camelcase_to_underscore


@pytest.mark.parametrize(
    "camel_str,expected",
    [
        ("startDate", "start_date"),
        ("localStartDate", "local_start_date"),
        ("hasAttachments", "has_attachments"),
        ("simple", "simple"),
        ("CamelCase", "camel_case"),
        ("already_snake_case", "already_snake_case"),
        ("", ""),
        ("A", "a"),
        ("TestABC", "test_a_b_c"),
        ("testABC", "test_a_b_c"),
        ("testA", "test_a"),
        ("TestA", "test_a"),
        ("test", "test"),
        ("Test", "test"),
        ("testID", "test_i_d"),
        ("IDTest", "i_d_test"),
    ],
)
def test_camelcase_to_underscore(camel_str, expected):
    """Test the camelcase_to_underscore function."""
    assert camelcase_to_underscore(camel_str) == expected
