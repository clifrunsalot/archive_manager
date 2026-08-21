import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_manager.admin.backfill_event_facts as backfill_event_facts
from archive_manager.core.event_manifests import save_manifests
from archive_manager.core.event_model import EventManifest, PageMetadata


class BackfillEventFactsTest(unittest.TestCase):
    def test_builds_facts_from_manifest_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            searchable = root / "searchable"
            searchable.mkdir()
            (searchable / "doc.txt").write_text(
                "12SEP24\nPERFORM MULTI POINT INSPECTION\nTOTAL CHARGES\n158.16",
                encoding="utf-8",
            )
            manifest_path = root / "events.json"
            manifest = EventManifest(
                event_id="event-1",
                event_type="automotive_service",
                pages=[PageMetadata("page-1.jpg", 1, 1)],
            )
            save_manifests(manifest_path, {"event-1": manifest})
            with (
                patch.object(backfill_event_facts, "MANIFEST_PATH", manifest_path),
                patch.object(backfill_event_facts, "SEARCHABLE_DIR", searchable),
                patch.object(backfill_event_facts, "load_ingest_cache", return_value={"doc": "page-1.jpg"}),
            ):
                result = backfill_event_facts.build_event_facts()

        self.assertEqual(result["event-1"]["total_charges"], "158.16")

    def test_missing_event_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.json"
            save_manifests(path, {})
            with patch.object(backfill_event_facts, "MANIFEST_PATH", path):
                with self.assertRaises(ValueError):
                    backfill_event_facts.build_event_facts("missing")


if __name__ == "__main__":
    unittest.main()