"""Tests for the SrpPassword class in pyicloud.srp_password module."""

import pytest

from pyicloud.srp_password import SrpPassword


def test_encode_raises_value_error_if_encrypt_info_not_set() -> None:
    """Test that encode raises ValueError if encrypt info is not set."""
    srp = SrpPassword("testpassword")
    with pytest.raises(ValueError, match="Encrypt info not set"):
        srp.encode()


@pytest.mark.parametrize(
    "password,salt,iterations,key_length,expected_length",
    [
        ("password123", b"salty", 1000, 32, 32),
        ("anotherpass", b"12345678", 500, 16, 16),
    ],
)
def test_encode_returns_correct_length(
    password: str, salt: bytes, iterations: int, key_length: int, expected_length: int
) -> None:
    """Test that encode returns bytes of the expected length."""
    srp = SrpPassword(password)
    srp.set_encrypt_info(salt, iterations, key_length)
    result = srp.encode()
    assert isinstance(result, bytes)
    assert len(result) == expected_length


def test_encode_consistency_for_same_input() -> None:
    """Test that encode returns the same result for the same input."""
    srp1 = SrpPassword("mypassword")
    srp2 = SrpPassword("mypassword")
    salt = b"abcdef"
    iterations = 1000
    key_length = 24
    srp1.set_encrypt_info(salt, iterations, key_length)
    srp2.set_encrypt_info(salt, iterations, key_length)
    assert srp1.encode() == srp2.encode()
