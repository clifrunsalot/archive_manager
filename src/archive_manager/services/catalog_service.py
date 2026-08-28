"""Authorization-aware event catalog operations."""

from __future__ import annotations

from typing import Any

from archive_manager.core.event_manifests import load_manifests
from archive_manager.core.event_manifests import find_manifest_for_source
from archive_manager.ingestion.ingest import load_ingest_cache
from archive_manager.security.access_policy import is_authorized, is_source_authorized


class CatalogService:
    """Read event manifests through the archive's authorization policy."""

    def __init__(self, manifest_path):
        self.manifest_path = manifest_path

    def visible_manifests(self, user: str) -> dict[str, Any]:
        manifests = load_manifests(self.manifest_path)
        return {
            event_id: manifest
            for event_id, manifest in manifests.items()
            if is_authorized(manifest, user)
        }

    def visible_artifacts(self, user: str) -> list[dict[str, str | None]]:
        """Return processed source filenames visible to one authenticated user."""
        manifests = load_manifests(self.manifest_path)
        artifacts = []
        for doc_id, source in sorted(load_ingest_cache().items(), key=lambda item: item[1].casefold()):
            manifest = find_manifest_for_source(manifests, source)
            if not is_source_authorized(manifest, user):
                continue
            artifacts.append({
                "doc_id": str(doc_id),
                "filename": str(source),
                "event_id": manifest.event_id if manifest else None,
            })
        return artifacts