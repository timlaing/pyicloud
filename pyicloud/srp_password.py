"""SRP password handling."""

from enum import Enum
from hashlib import pbkdf2_hmac, sha256


class SrpProtocolType(Enum):
    """SRP password types."""

    S2K = "s2k"
    S2K_FO = "s2k_fo"


class SrpPassword:
    """SRP password."""

    def __init__(self, password: str) -> None:
        self._password_hash: bytes = sha256(password.encode("utf-8")).digest()
        self.salt: bytes | None = None
        self.iterations: int | None = None
        self.key_length: int | None = None
        self.protocol: SrpProtocolType | None = None

    def set_encrypt_info(
        self, salt: bytes, iterations: int, key_length: int, protocol: SrpProtocolType
    ) -> None:
        """Set encrypt info."""
        self.salt = salt
        self.iterations = iterations
        self.key_length = key_length
        self.protocol = protocol

    def encode(self) -> bytes:
        """Encode password."""
        if self.salt is None or self.iterations is None or self.key_length is None:
            raise ValueError("Encrypt info not set")

        password_digest: bytes | None = None

        if self.protocol == SrpProtocolType.S2K_FO:
            password_digest = self._password_hash.hex().encode()
        elif self.protocol == SrpProtocolType.S2K:
            password_digest = self._password_hash

        if password_digest is None:
            raise ValueError("Unsupported SrpPassword type")

        return pbkdf2_hmac(
            "sha256",
            password_digest,
            self.salt,
            self.iterations,
            self.key_length,
        )
