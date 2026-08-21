#!/usr/bin/env python3
"""Clear the archive index and generated local records.

This script is intended for a controlled reset of the archive search system. It
removes the Qdrant collection used to store vector embeddings, clears the local
ingest cache, and deletes generated document files in the archive and searchable
areas.

Use this when you want to start indexing from a clean state or when you need to
remove the current records from the local environment.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

import requests

from archive_manager.paths import PROJECT_ROOT

ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", PROJECT_ROOT / "ARCHIVE"))
SOURCE_DIR = Path(os.environ.get("SOURCE_DIR", PROJECT_ROOT / "data" / "source"))
SEARCHABLE_DIR = Path(os.environ.get("SEARCHABLE_DIR", PROJECT_ROOT / "data" / "searchable"))
LOGS_DIR = Path(os.environ.get("LOGS_DIR", PROJECT_ROOT / "logs"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR", PROJECT_ROOT / "data" / ".ingest_cache"))
CACHE_FILE = CACHE_DIR / "ingested.json"
EVENT_FACTS_FILE = PROJECT_ROOT / "data" / ".event_facts" / "facts.json"
EVENT_MANIFEST_FILE = Path(
    os.environ.get("EVENT_MANIFEST_PATH", PROJECT_ROOT / "data" / "events.json")
)

DEFAULT_QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
DEFAULT_QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "archive_chunks")


def qdrant_request_headers() -> dict[str, str]:
    """Return authenticated request headers when Qdrant auth is configured."""
    api_key = os.environ.get("QDRANT_API_KEY", "")
    return {"api-key": api_key} if api_key else {}


def clear_directory_contents(directory: Path) -> None:
    """Delete all files and directories inside a directory without removing the directory itself."""
    if not directory.exists():
        return

    for child in list(directory.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def delete_qdrant_collection(base_url: str, collection_name: str) -> None:
    """Delete the Qdrant collection that stores archive chunk vectors."""
    qdrant_url = base_url.rstrip("/")
    endpoint = f"{qdrant_url}/collections/{collection_name}"

    try:
        response = requests.delete(endpoint, timeout=30, headers=qdrant_request_headers())
    except requests.RequestException as exc:  # pragma: no cover - network failure path
        raise RuntimeError(f"Failed to reach Qdrant at {endpoint}: {exc}") from exc

    if response.status_code in {200, 202, 204}:
        return

    if response.status_code == 404:
        print(f"Qdrant collection not found: {collection_name}")
        return

    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    raise RuntimeError(f"Failed to delete Qdrant collection {collection_name}: {response.status_code} {detail}")


def clear_ingest_cache(cache_file: Path) -> None:
    """Remove the local ingest cache used to skip already-indexed files."""
    if cache_file.exists():
        cache_file.unlink()


def confirm_or_exit(force: bool, prompt_text: str) -> None:
    """Require explicit confirmation before destructive actions are applied."""
    if force:
        return

    answer = input(f"{prompt_text} [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        raise SystemExit("Reset cancelled.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset the archive index and generated archive records to an empty state."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the actions that would be taken without deleting anything.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt before deleting data.",
    )
    parser.add_argument(
        "--qdrant-url",
        default=DEFAULT_QDRANT_URL,
        help=f"Qdrant base URL. Default: {DEFAULT_QDRANT_URL}",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_QDRANT_COLLECTION,
        help=f"Qdrant collection name. Default: {DEFAULT_QDRANT_COLLECTION}",
    )
    parser.add_argument(
        "--no-qdrant",
        action="store_true",
        help="Skip clearing the Qdrant collection.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Skip clearing the local ingest cache file.",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="Skip deleting files inside the archive directory.",
    )
    parser.add_argument(
        "--no-source",
        action="store_true",
        help="Skip deleting files inside the source directory.",
    )
    parser.add_argument(
        "--no-searchable",
        action="store_true",
        help="Skip deleting files inside the searchable directory.",
    )
    parser.add_argument(
        "--no-event-facts",
        action="store_true",
        help="Skip deleting persisted EventFacts metadata.",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Skip deleting the event manifest.",
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="Skip deleting generated processing and audit logs.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.dry_run:
        print("Dry run: the following actions would be taken:")
    else:
        confirm_or_exit(args.force, "This will permanently delete archive records and indexed content. Continue?")

    actions: list[str] = []

    if not args.no_qdrant:
        actions.append(f"Delete Qdrant collection '{args.collection}' from {args.qdrant_url}")
    if not args.no_cache:
        actions.append(f"Delete ingest cache at {CACHE_FILE}")
    if not args.no_archive:
        actions.append(f"Clear contents of {ARCHIVE_DIR}")
    if not args.no_source:
        actions.append(f"Clear contents of {SOURCE_DIR}")
    if not args.no_searchable:
        actions.append(f"Clear contents of {SEARCHABLE_DIR}")
    if not args.no_event_facts:
        actions.append(f"Delete EventFacts metadata at {EVENT_FACTS_FILE}")
    if not args.no_manifest:
        actions.append(f"Delete event manifest at {EVENT_MANIFEST_FILE}")
    if not args.no_logs:
        actions.append(f"Clear generated logs in {LOGS_DIR}")

    for action in actions:
        print(f"- {action}")

    if args.dry_run:
        return 0

    try:
        if not args.no_qdrant:
            delete_qdrant_collection(args.qdrant_url, args.collection)
            print(f"Deleted Qdrant collection: {args.collection}")

        if not args.no_cache:
            clear_ingest_cache(CACHE_FILE)
            print(f"Removed ingest cache: {CACHE_FILE}")

        if not args.no_archive:
            clear_directory_contents(ARCHIVE_DIR)
            print(f"Cleared archive directory: {ARCHIVE_DIR}")

        if not args.no_source:
            clear_directory_contents(SOURCE_DIR)
            print(f"Cleared source directory: {SOURCE_DIR}")

        if not args.no_searchable:
            clear_directory_contents(SEARCHABLE_DIR)
            print(f"Cleared searchable directory: {SEARCHABLE_DIR}")

        if not args.no_event_facts and EVENT_FACTS_FILE.exists():
            EVENT_FACTS_FILE.unlink()
            print(f"Removed EventFacts metadata: {EVENT_FACTS_FILE}")

        if not args.no_manifest and EVENT_MANIFEST_FILE.exists():
            EVENT_MANIFEST_FILE.unlink()
            print(f"Removed event manifest: {EVENT_MANIFEST_FILE}")

        if not args.no_logs:
            clear_directory_contents(LOGS_DIR)
            print(f"Cleared logs directory: {LOGS_DIR}")

    except Exception as exc:  # pragma: no cover - CLI error path
        print(f"Reset failed: {exc}", file=sys.stderr)
        return 1

    print("Archive reset complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
