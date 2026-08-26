"""Delete one manifest event and its derived local/indexed data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import requests

from archive_manager.core.event_manifests import load_manifests, save_manifests
from archive_manager.core.event_facts import load_event_facts, save_event_facts
from archive_manager.security.access_policy import is_authorized
from archive_manager.ingestion.ingest import (
    ARCHIVE_DIR,
    CACHE_DIR,
    QDRANT_COLLECTION,
    QDRANT_URL,
    qdrant_request_headers,
    SEARCHABLE_DIR,
    SOURCE_DIR,
    load_ingest_cache,
    save_ingest_cache,
)
from archive_manager.paths import PROJECT_ROOT

MANIFEST_PATH = Path(os.environ.get("EVENT_MANIFEST_PATH", PROJECT_ROOT / "data" / "events.json"))


def delete_qdrant_event(event_id: str, base_url: str = QDRANT_URL, collection: str = QDRANT_COLLECTION):
    """Delete indexed chunks carrying an event ID."""
    endpoint = f"{base_url.rstrip('/')}/collections/{collection}/points/delete?wait=true"
    response = requests.post(
        endpoint,
        json={"filter": {"must": [{"key": "event_id", "match": {"value": event_id}}]}},
        timeout=30,
        headers=qdrant_request_headers(),
    )
    response.raise_for_status()


def delete_event_files(event_id: str, filenames: list[str], cache: dict[str, str]):
    """Remove event source files and derived files, returning removed paths."""
    removed = []
    document_ids = [doc_id for doc_id, source in cache.items() if source in filenames]

    for filename in filenames:
        path = ARCHIVE_DIR / filename
        if path.exists() and path.is_file():
            path.unlink()
            removed.append(path)

    for doc_id in document_ids:
        for directory in (SOURCE_DIR, SEARCHABLE_DIR):
            for path in directory.glob(f"{doc_id}.*"):
                if path.is_file():
                    path.unlink()
                    removed.append(path)
        cache.pop(doc_id, None)

    return removed


def verify_deleted_local_data(filenames: list[str], document_ids: list[str], cache: dict[str, str]) -> None:
    """Fail if any source, derived file, or cache entry remains after deletion."""
    remaining_paths = [
        path
        for directory, names in ((ARCHIVE_DIR, filenames), (SOURCE_DIR, document_ids), (SEARCHABLE_DIR, document_ids))
        for name in names
        for path in ([directory / name] if directory == ARCHIVE_DIR else directory.glob(f"{name}.*"))
        if path.exists()
    ]
    remaining_cache = [doc_id for doc_id in document_ids if doc_id in cache]
    if remaining_paths or remaining_cache:
        raise RuntimeError(
            f"Deletion verification failed: paths={len(remaining_paths)} cache_entries={len(remaining_cache)}"
        )


def delete_event(event_id: str, *, dry_run: bool = False, base_url: str = QDRANT_URL, collection: str = QDRANT_COLLECTION):
    """Delete a manifest event and all locally derivable representations."""
    manifests = load_manifests(MANIFEST_PATH)
    manifest = manifests.get(event_id)
    if manifest is None:
        raise ValueError(f"Event manifest not found: {event_id}")
    if not is_authorized(manifest):
        raise PermissionError(f"Current user is not authorized to delete event: {event_id}")

    filenames = [page.source_filename for page in manifest.pages]
    cache = load_ingest_cache()
    document_ids = [doc_id for doc_id, source in cache.items() if source in filenames]
    if dry_run:
        return {"event_id": event_id, "filenames": filenames, "document_ids": document_ids}

    delete_qdrant_event(event_id, base_url=base_url, collection=collection)
    delete_event_files(event_id, filenames, cache)
    save_ingest_cache(cache)
    verify_deleted_local_data(filenames, document_ids, cache)
    event_facts = load_event_facts()
    if event_id in event_facts:
        del event_facts[event_id]
        save_event_facts(event_facts)
    del manifests[event_id]
    save_manifests(MANIFEST_PATH, manifests)
    return {"event_id": event_id, "filenames": filenames, "document_ids": document_ids}


def main():
    parser = argparse.ArgumentParser(description="Delete one archive event and its indexed data")
    parser.add_argument("event_id_positional", nargs="?")
    parser.add_argument("--event-id", dest="event_id_named")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--qdrant-url", default=QDRANT_URL)
    parser.add_argument("--collection", default=QDRANT_COLLECTION)
    args = parser.parse_args()

    if args.event_id_positional and args.event_id_named:
        parser.error("provide the event ID either positionally or with --event-id, not both")
    event_id = args.event_id_named or args.event_id_positional
    if not event_id:
        parser.error("an event ID is required; use --event-id EVENT_ID")

    if not args.dry_run and not args.force:
        answer = input(f"Permanently delete event {event_id}? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            raise SystemExit("Deletion cancelled.")

    result = delete_event(
        event_id,
        dry_run=args.dry_run,
        base_url=args.qdrant_url,
        collection=args.collection,
    )
    action = "would delete" if args.dry_run else "deleted"
    print(f"Event {result['event_id']} {action}: {len(result['filenames'])} files")


if __name__ == "__main__":
    main()