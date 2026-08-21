"""Backfill ingest-time EventFacts for existing manifest events."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from archive_manager.core.event_facts import extract_event_facts, load_event_facts, save_event_facts
from archive_manager.core.event_manifests import load_manifests
from archive_manager.ingestion.ingest import SEARCHABLE_DIR, load_ingest_cache
from archive_manager.paths import PROJECT_ROOT

MANIFEST_PATH = Path(os.environ.get("EVENT_MANIFEST_PATH", PROJECT_ROOT / "data" / "events.json"))


def build_event_facts(event_id: str | None = None) -> dict:
    """Extract facts for all or one manifest event from existing OCR sidecars."""
    manifests = load_manifests(MANIFEST_PATH)
    cache = load_ingest_cache()
    source_to_doc_id = {source: doc_id for doc_id, source in cache.items()}
    selected = [event_id] if event_id else sorted(manifests)
    results = {}
    for current_id in selected:
        manifest = manifests.get(current_id)
        if manifest is None:
            raise ValueError(f"Event manifest not found: {current_id}")
        pages = []
        for page in manifest.ordered_pages():
            doc_id = source_to_doc_id.get(page.source_filename)
            if not doc_id:
                continue
            sidecar = SEARCHABLE_DIR / f"{doc_id}.txt"
            if sidecar.exists():
                pages.append({"page": page.page_number, "text": sidecar.read_text(encoding="utf-8")})
        results[current_id] = extract_event_facts(current_id, manifest.event_type, pages)
    return results


def main():
    parser = argparse.ArgumentParser(description="Backfill EventFacts from existing OCR sidecars")
    parser.add_argument("--event-id", help="Backfill only one event")
    parser.add_argument("--dry-run", action="store_true", help="Extract and report without saving")
    args = parser.parse_args()
    facts = build_event_facts(args.event_id)
    if not args.dry_run:
        existing = load_event_facts()
        existing.update(facts)
        save_event_facts(existing)
    print(f"Backfilled {len(facts)} event(s){' (dry run)' if args.dry_run else ''}.")


if __name__ == "__main__":
    main()