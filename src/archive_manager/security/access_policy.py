"""Local event authorization policy for sensitive archive records."""

import os
from typing import Any


def current_user() -> str:
    """Return the configured local identity used for event access checks."""
    return os.environ.get("ARCHIVE_AUDIT_USER", "local-user")


def is_authorized(manifest: Any, user: str | None = None) -> bool:
    """Return whether a manifest is visible to the current user.

    Compatibility mode keeps existing unclassified records available. Strict mode
    requires an explicit ``allowed_users`` list in the manifest metadata.
    """
    if os.environ.get("ARCHIVE_AUTH_MODE", "compat").lower() not in {"strict", "enforced"}:
        return True
    allowed_users = manifest.metadata.get("allowed_users", [])
    if not isinstance(allowed_users, list):
        return False
    return (user or current_user()) in {str(value) for value in allowed_users}


def authorized_event_ids(manifests: dict[str, Any], user: str | None = None) -> set[str]:
    """Return event IDs visible to one user."""
    return {
        event_id
        for event_id, manifest in manifests.items()
        if is_authorized(manifest, user)
    }