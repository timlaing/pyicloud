"""SRP password handling."""

import hashlib


class SrpPassword:
    """SRP password."""

    def __init__(self, password: str) -> None:
        self._password_hash: bytes = hashlib.sha256(password.encode("utf-8")).digest()
        self.salt: bytes | None = None
        self.iterations: int | None = None
        self.key_length: int | None = None

    def set_encrypt_info(self, salt: bytes, iterations: int, key_length: int) -> None:
        """Set encrypt info."""
        self.salt = salt
        self.iterations = iterations
        self.key_length = key_length

    def encode(
        self,
    ) -> bytes:
        """Encode password."""
        if self.salt is None or self.iterations is None or self.key_length is None:
            raise ValueError("Encrypt info not set")

        return hashlib.pbkdf2_hmac(
            "sha256",
            self._password_hash,
            self.salt,
            self.iterations,
            self.key_length,
        )
