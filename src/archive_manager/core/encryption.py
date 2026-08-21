"""Optional authenticated encryption for sensitive local archive metadata."""

import os

from cryptography.fernet import Fernet, InvalidToken


ENCRYPTED_PREFIX = b"ARCHIVE-FERNET-V1\n"


def _key() -> bytes | None:
    value = os.environ.get("ARCHIVE_ENCRYPTION_KEY", "").strip()
    return value.encode("ascii") if value else None


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt bytes when a valid archive key is configured."""
    key = _key()
    if key is None:
        return data
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise ValueError("ARCHIVE_ENCRYPTION_KEY must be a valid Fernet key") from exc
    return ENCRYPTED_PREFIX + Fernet(key).encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    """Decrypt archive bytes, failing closed when a key is unavailable or wrong."""
    if not data.startswith(ENCRYPTED_PREFIX):
        return data
    key = _key()
    if key is None:
        raise RuntimeError("ARCHIVE_ENCRYPTION_KEY is required to read encrypted archive metadata")
    try:
        return Fernet(key).decrypt(data[len(ENCRYPTED_PREFIX) :])
    except (InvalidToken, ValueError, TypeError) as exc:
        raise RuntimeError("Unable to decrypt archive metadata with ARCHIVE_ENCRYPTION_KEY") from exc


def generate_key() -> str:
    """Generate a Fernet key suitable for ARCHIVE_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode("ascii")