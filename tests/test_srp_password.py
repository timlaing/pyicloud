"""Tests for the SrpPassword class in pyicloud.srp_password module."""

from hashlib import sha256
from unittest.mock import patch

import pytest

from pyicloud.srp_password import SrpPassword, SrpProtocolType


def test_encode_raises_value_error_if_encrypt_info_not_set() -> None:
    """Test that encode raises ValueError if encrypt info is not set."""
    srp = SrpPassword("testpassword")
    with pytest.raises(ValueError, match="Encrypt info not set"):
        srp.encode()


def test_encode_raises_value_error_if_protocol_not_valid() -> None:
    """Test that encode raises ValueError if protocol is not valid."""

    srp = SrpPassword("testpassword")
    srp.set_encrypt_info("abc", "1", "32", None)  # type: ignore

    with pytest.raises(ValueError, match="Unsupported SrpPassword type"):
        srp.encode()


@pytest.mark.parametrize(
    "password,salt,iterations,key_length,expected_length,protocol",
    [
        ("password123", b"salty", 1000, 32, 32, SrpProtocolType.S2K),
        ("anotherpass", b"12345678", 500, 16, 16, SrpProtocolType.S2K),
        ("anotherpass", b"12345678", 500, 16, 16, SrpProtocolType.S2K_FO),
    ],
)
def test_encode_returns_correct_length(
    password: str,
    salt: bytes,
    iterations: int,
    key_length: int,
    expected_length: int,
    protocol: SrpProtocolType,
) -> None:
    """Test that encode returns bytes of the expected length."""
    srp = SrpPassword(password)
    srp.set_encrypt_info(salt, iterations, key_length, protocol)
    result: bytes = srp.encode()
    assert isinstance(result, bytes)
    assert len(result) == expected_length


def test_encode_consistency_for_same_input() -> None:
    """Test that encode returns the same result for the same input."""
    srp1 = SrpPassword("mypassword")
    srp2 = SrpPassword("mypassword")
    salt = b"abcdef"
    iterations = 1000
    key_length = 24
    srp1.set_encrypt_info(salt, iterations, key_length, SrpProtocolType.S2K)
    srp2.set_encrypt_info(salt, iterations, key_length, SrpProtocolType.S2K)
    assert srp1.encode() == srp2.encode()
    srp1.set_encrypt_info(salt, iterations, key_length, SrpProtocolType.S2K_FO)
    srp2.set_encrypt_info(salt, iterations, key_length, SrpProtocolType.S2K_FO)
    assert srp1.encode() == srp2.encode()


def test_srp_password_digest() -> None:
    """Test that the SrpPassword digest method works as expected."""
    password = "securepassword"
    salt = b"saltysalt"
    iterations = 2000
    key_length = 32

    password_digest: bytes = sha256(password.encode("utf-8")).digest()
    password_digest_hex: bytes = sha256(password.encode("utf-8")).hexdigest().encode()

    with patch("pyicloud.srp_password.pbkdf2_hmac") as mock_pbkdf2_hmac:
        srp = SrpPassword(password)
        srp.set_encrypt_info(salt, iterations, key_length, SrpProtocolType.S2K)
        _ = srp.encode()

        mock_pbkdf2_hmac.assert_called_with(
            "sha256", password_digest, salt, iterations, key_length
        )

        srp.set_encrypt_info(salt, iterations, key_length, SrpProtocolType.S2K_FO)
        _ = srp.encode()
        mock_pbkdf2_hmac.assert_called_with(
            "sha256", password_digest_hex, salt, iterations, key_length
        )
