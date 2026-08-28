"""Loopback-only archive reset operations for the local UI."""

from __future__ import annotations

import secrets
from pathlib import Path

from archive_manager.admin import reset_archive


class ResetService:
    """Preview and execute the existing full archive reset implementation."""

    def __init__(self):
        self._tokens: set[str] = set()

    def preview(self) -> dict:
        actions = [
            f"Delete Qdrant collection '{reset_archive.DEFAULT_QDRANT_COLLECTION}'",
            f"Remove ingest cache: {reset_archive.CACHE_FILE}",
            f"Clear archive directory: {reset_archive.ARCHIVE_DIR}",
            f"Clear source directory: {reset_archive.SOURCE_DIR}",
            f"Clear searchable directory: {reset_archive.SEARCHABLE_DIR}",
            f"Delete EventFacts metadata: {reset_archive.EVENT_FACTS_FILE}",
            f"Delete event manifest: {reset_archive.EVENT_MANIFEST_FILE}",
            f"Clear generated logs: {reset_archive.LOGS_DIR}",
        ]
        token = secrets.token_urlsafe(32)
        self._tokens.add(token)
        return {"actions": actions, "confirmation_token": token}

    def execute(self, token: str, confirmation: str) -> dict:
        if token not in self._tokens:
            raise PermissionError("Reset confirmation token is invalid or expired")
        if confirmation != "RESET ARCHIVE":
            raise ValueError("Type exactly: RESET ARCHIVE")
        self._tokens.remove(token)
        reset_archive.delete_qdrant_collection(
            reset_archive.DEFAULT_QDRANT_URL,
            reset_archive.DEFAULT_QDRANT_COLLECTION,
        )
        reset_archive.clear_ingest_cache(reset_archive.CACHE_FILE)
        for directory in (
            reset_archive.ARCHIVE_DIR,
            reset_archive.SOURCE_DIR,
            reset_archive.SEARCHABLE_DIR,
            reset_archive.LOGS_DIR,
        ):
            reset_archive.clear_directory_contents(directory)
        for path in (reset_archive.EVENT_FACTS_FILE, reset_archive.EVENT_MANIFEST_FILE):
            if path.exists():
                path.unlink()
        return {"status": "completed", "message": "Archive reset complete."}
