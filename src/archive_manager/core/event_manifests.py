"""Persistence and lookup helpers for domain-neutral event manifests."""

import json
from pathlib import Path

from archive_manager.core.encryption import decrypt_bytes, encrypt_bytes
from archive_manager.core.event_model import EventManifest


def load_manifests(path: Path) -> dict[str, EventManifest]:
    """Load manifests from a JSON object keyed by event ID."""
    if not path.exists():
        return {}
    try:
        data = json.loads(decrypt_bytes(path.read_bytes()).decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    manifests = {}
    for event_id, raw_manifest in data.items():
        if not isinstance(raw_manifest, dict):
            continue
        manifest_data = {**raw_manifest, "event_id": event_id}
        manifests[event_id] = EventManifest.from_dict(manifest_data)
    return manifests


def save_manifests(path: Path, manifests: dict[str, EventManifest]):
    """Persist manifests atomically as readable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {event_id: manifest.to_dict() for event_id, manifest in sorted(manifests.items())}
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    serialized = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    temporary_path.write_bytes(encrypt_bytes(serialized))
    temporary_path.replace(path)


def find_manifest_for_source(
    manifests: dict[str, EventManifest], source_filename: str
) -> EventManifest | None:
    """Return the event manifest containing a source filename, if any."""
    return next(
        (manifest for manifest in manifests.values() if manifest.page_for(source_filename)),
        None,
    )