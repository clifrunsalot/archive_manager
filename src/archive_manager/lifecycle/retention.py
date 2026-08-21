"""Retention policy helpers for event manifests."""

from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def expiration_for(manifest: Any) -> datetime | None:
    """Return an event expiration timestamp from explicit manifest policy."""
    metadata = manifest.metadata
    explicit = _parse_timestamp(metadata.get("expires_at"))
    if explicit:
        return explicit
    retention_days = metadata.get("retention_days")
    created_at = _parse_timestamp(metadata.get("created_at"))
    if isinstance(retention_days, int) and retention_days >= 0 and created_at:
        return created_at + timedelta(days=retention_days)
    return None


def is_expired(manifest: Any, now: datetime | None = None) -> bool:
    """Return whether an event has an explicit policy and is past expiration."""
    expiration = expiration_for(manifest)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return expiration is not None and expiration <= current