"""Runtime security requirements for sensitive archive mode."""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

from archive_manager.core.encryption import _key


SENSITIVE_MODES = {"strict", "enforced", "sensitive"}


def sensitive_mode() -> bool:
    """Return whether sensitive-data protections are required."""
    return os.environ.get("ARCHIVE_SECURITY_MODE", "").lower() in SENSITIVE_MODES


def _is_local_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "host.docker.internal"}:
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_sensitive_configuration() -> None:
    """Fail closed when required sensitive-mode controls are absent or unsafe."""
    if not sensitive_mode():
        return
    if _key() is None:
        raise RuntimeError("ARCHIVE_ENCRYPTION_KEY is required in sensitive mode")
    if not os.environ.get("QDRANT_API_KEY", "").strip():
        raise RuntimeError("QDRANT_API_KEY is required in sensitive mode")
    ollama_base = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
    if not _is_local_url(ollama_base):
        raise RuntimeError("OLLAMA_BASE must use a local URL in sensitive mode")
    paddleocr_url = os.environ.get("PADDLEOCR_SERVICE_URL", "http://localhost:8000")
    if not _is_local_url(paddleocr_url):
        raise RuntimeError("PADDLEOCR_SERVICE_URL must use a local URL in sensitive mode")
