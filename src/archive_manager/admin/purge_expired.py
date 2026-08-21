"""Preview or delete events that have passed their explicit retention policy."""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import archive_manager.admin.delete_event as delete_event
from archive_manager.core.event_manifests import load_manifests
from archive_manager.lifecycle.retention import expiration_for, is_expired
from archive_manager.paths import PROJECT_ROOT

MANIFEST_PATH = Path(os.environ.get("EVENT_MANIFEST_PATH", PROJECT_ROOT / "data" / "events.json"))


def expired_event_ids(now: datetime | None = None) -> list[str]:
    """Return explicitly governed events whose retention period has expired."""
    manifests = load_manifests(MANIFEST_PATH)
    return sorted(
        event_id
        for event_id, manifest in manifests.items()
        if is_expired(manifest, now)
    )


def main():
    parser = argparse.ArgumentParser(description="Purge events past their retention policy")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    manifests = load_manifests(MANIFEST_PATH)
    event_ids = expired_event_ids()

    if not event_ids:
        print("No events have passed an explicit retention policy.")
        return

    for event_id in event_ids:
        expiration = expiration_for(manifests[event_id])
        print(f"- {event_id}: expires {expiration.isoformat() if expiration else 'unknown'}")

    if args.dry_run:
        return
    if not args.force:
        answer = input("Permanently delete these expired events? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            raise SystemExit("Purge cancelled.")

    for event_id in event_ids:
        delete_event.MANIFEST_PATH = MANIFEST_PATH
        delete_event.delete_event(event_id)
    print(f"Purged {len(event_ids)} expired event(s).")


if __name__ == "__main__":
    main()