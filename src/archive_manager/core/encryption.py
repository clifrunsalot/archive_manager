"""Optional authenticated encryption for sensitive local archive metadata."""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from archive_manager.paths import PROJECT_ROOT

ENCRYPTED_PREFIX = b"ARCHIVE-FERNET-V1\n"
KEY_FILE = PROJECT_ROOT / ".archive_key"


def get_or_create_key() -> str | None:
    """Get configured Fernet key, falling back to persistent .archive_key in sensitive mode."""
    if "ARCHIVE_ENCRYPTION_KEY" in os.environ:
        value = os.environ["ARCHIVE_ENCRYPTION_KEY"].strip()
        return value if value else None

    from archive_manager.security.security_config import sensitive_mode

    if sensitive_mode():
        if KEY_FILE.exists():
            try:
                val = KEY_FILE.read_text(encoding="utf-8").strip()
                if val:
                    return val
            except OSError:
                pass

        try:
            new_key = generate_key()
            KEY_FILE.write_text(new_key + "\n", encoding="utf-8")
            KEY_FILE.chmod(0o600)
            return new_key
        except OSError:
            return None

    return None


def _key() -> bytes | None:
    value = get_or_create_key()
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