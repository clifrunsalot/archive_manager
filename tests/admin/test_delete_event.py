import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_manager.admin.delete_event as delete_event
import os
from archive_manager.core.event_manifests import load_manifests, save_manifests
from archive_manager.core.event_model import EventManifest, PageMetadata


class DeleteEventTest(unittest.TestCase):
    def test_strict_mode_blocks_unauthorized_delete(self):
        manifest = EventManifest(
            event_id="event-1",
            metadata={"allowed_users": ["alice"]},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.json"
            save_manifests(path, {manifest.event_id: manifest})
            with (
                patch.object(delete_event, "MANIFEST_PATH", path),
                patch.dict(os.environ, {"ARCHIVE_AUTH_MODE": "strict", "ARCHIVE_AUDIT_USER": "bob"}, clear=False),
            ):
                with self.assertRaises(PermissionError):
                    delete_event.delete_event("event-1", dry_run=True)

    def test_dry_run_does_not_modify_manifest(self):
        manifest = EventManifest(
            event_id="event-1",
            pages=[PageMetadata("page-001.jpg", 1, 1)],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.json"
            save_manifests(path, {manifest.event_id: manifest})
            with patch.object(delete_event, "MANIFEST_PATH", path):
                result = delete_event.delete_event("event-1", dry_run=True)

            self.assertEqual(result["filenames"], ["page-001.jpg"])
            self.assertIn("event-1", load_manifests(path))

    def test_delete_removes_manifest_cache_files_and_index(self):
        manifest = EventManifest(
            event_id="event-1",
            pages=[PageMetadata("page-001.jpg", 1, 1)],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "events.json"
            archive = root / "archive"
            source = root / "source"
            searchable = root / "searchable"
            for directory in (archive, source, searchable):
                directory.mkdir()
            (archive / "page-001.jpg").write_bytes(b"image")
            (source / "abc.pdf").write_bytes(b"pdf")
            (searchable / "abc.txt").write_text("ocr", encoding="utf-8")
            save_manifests(manifest_path, {manifest.event_id: manifest})

            with (
                patch.object(delete_event, "MANIFEST_PATH", manifest_path),
                patch.object(delete_event, "ARCHIVE_DIR", archive),
                patch.object(delete_event, "SOURCE_DIR", source),
                patch.object(delete_event, "SEARCHABLE_DIR", searchable),
                patch.object(delete_event, "load_ingest_cache", return_value={"abc": "page-001.jpg"}),
                patch.object(delete_event, "save_ingest_cache") as save_cache,
                patch.object(delete_event, "load_event_facts", return_value={"event-1": {"domain": "automotive_service"}}),
                patch.object(delete_event, "save_event_facts") as save_facts,
                patch.object(delete_event, "delete_qdrant_event") as delete_qdrant,
            ):
                delete_event.delete_event("event-1")

            delete_qdrant.assert_called_once()
            save_cache.assert_called_once_with({})
            save_facts.assert_called_once_with({})
            self.assertFalse((archive / "page-001.jpg").exists())
            self.assertFalse((source / "abc.pdf").exists())
            self.assertFalse((searchable / "abc.txt").exists())
            self.assertNotIn("event-1", load_manifests(manifest_path))

    def test_delete_verification_fails_when_derived_data_remains(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for directory in (root / "archive", root / "source", root / "searchable"):
                directory.mkdir()
            (root / "searchable" / "abc.txt").write_text("still present", encoding="utf-8")
            with (
                patch.object(delete_event, "ARCHIVE_DIR", root / "archive"),
                patch.object(delete_event, "SOURCE_DIR", root / "source"),
                patch.object(delete_event, "SEARCHABLE_DIR", root / "searchable"),
            ):
                with self.assertRaisesRegex(RuntimeError, "Deletion verification failed"):
                    delete_event.verify_deleted_local_data([], ["abc"], {"abc": "page.jpg"})


if __name__ == "__main__":
    unittest.main()