"""Local event authorization policy for sensitive archive records."""

import getpass
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from archive_manager.lifecycle.retention import is_expired


_request_user: ContextVar[str | None] = ContextVar("archive_request_user", default=None)


def current_user() -> str:
    """Return the configured local identity used for event access checks."""
    return _request_user.get() or os.environ.get("ARCHIVE_AUDIT_USER", "").strip() or getpass.getuser()


@contextmanager
def as_user(user: str):
    """Apply one authenticated identity to the current request context."""
    token = _request_user.set(user)
    try:
        yield
    finally:
        _request_user.reset(token)


def strict_mode() -> bool:
    """Return whether archive access must fail closed for unclassified sources."""
    mode = os.environ.get("ARCHIVE_AUTH_MODE", "compat").strip().lower()
    return mode in {"strict", "enforced"}


def is_authorized(manifest: Any, user: str | None = None) -> bool:
    """Return whether a manifest is visible to the current user.

    Compatibility mode keeps existing unclassified records available. Strict mode
    enforces explicit ``allowed_users`` lists when present in manifest metadata.
    """
    if not strict_mode():
        return True
    if manifest is None or not hasattr(manifest, "metadata"):
        return False
    if "allowed_users" not in manifest.metadata:
        return True
    allowed_users = manifest.metadata.get("allowed_users")
    if not isinstance(allowed_users, list):
        return False
    return (user or current_user()) in {str(value) for value in allowed_users}


def is_source_authorized(manifest: Any, user: str | None = None) -> bool:
    """Apply authorization to a source, denying unmanifested sources in strict mode."""
    if manifest is not None and is_expired(manifest):
        return False
    return True if manifest is None and not strict_mode() else is_authorized(manifest, user)


def authorized_event_ids(manifests: dict[str, Any], user: str | None = None) -> set[str]:
    """Return event IDs visible to one user."""
    return {
        event_id
        for event_id, manifest in manifests.items()
        if is_authorized(manifest, user) and not is_expired(manifest)
    }