import tempfile
import unittest
from pathlib import Path

from archive_manager.core.event_manifests import find_manifest_for_source, load_manifests, save_manifests
from archive_manager.core.event_model import EventManifest, PageMetadata
from archive_manager.ingestion.intake import create_manifest
from archive_manager.ingestion.intake import collect_input_files, natural_sort_key
from unittest.mock import patch

import archive_manager.retrieval.query as query


class EventManifestTest(unittest.TestCase):
    def test_multiple_events_keep_pages_separate_and_ordered(self):
        repair = create_manifest(
            "repair-2025-11-24",
            "automotive_service",
            ["repair-002.jpg", "repair-001.jpg"],
        )
        bank = create_manifest(
            "bank-2025-11",
            "banking_statement",
            ["bank-001.pdf", "bank-002.pdf"],
        )

        self.assertEqual(
            [page.source_filename for page in repair.ordered_pages()],
            ["repair-002.jpg", "repair-001.jpg"],
        )
        self.assertEqual(bank.page_for("bank-002.pdf").page_number, 2)
        self.assertIsNone(repair.page_for("bank-001.pdf"))

    def test_manifest_round_trip(self):
        manifest = EventManifest(
            event_id="medical-1",
            event_type="medical",
            subject_ref="subject-1",
            pages=[PageMetadata("visit-001.pdf", 1, 1)],
            metadata={"source": "intake"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.json"
            save_manifests(path, {manifest.event_id: manifest})
            loaded = load_manifests(path)

        self.assertEqual(loaded["medical-1"].event_type, "medical")
        self.assertEqual(loaded["medical-1"].subject_ref, "subject-1")
        self.assertEqual(find_manifest_for_source(loaded, "visit-001.pdf").event_id, "medical-1")

    def test_rejects_duplicate_page_numbers(self):
        with self.assertRaises(ValueError):
            EventManifest(
                event_id="duplicate-pages",
                pages=[
                    PageMetadata("one.pdf", 1, 2),
                    PageMetadata("two.pdf", 1, 2),
                ],
            )

    def test_query_groups_manifest_pages_as_one_event(self):
        manifest = create_manifest(
            "repair-event",
            "automotive_service",
            ["page-001.jpg", "page-002.jpg"],
        )
        with patch.object(query, "load_ingest_cache", return_value={}), \
             patch.object(query, "load_manifests", return_value={"repair-event": manifest}):
            groups = query._group_sources_into_records(["page-002.jpg", "page-001.jpg"])

        self.assertEqual(groups, [("event:repair-event", ["page-001.jpg", "page-002.jpg"])])

    def test_collect_input_files_naturally_sorts_page_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "page-10.jpg").write_bytes(b"10")
            (root / "page-2.jpg").write_bytes(b"2")
            (root / "page-1.jpg").write_bytes(b"1")
            (root / "notes.txt").write_text("ignore", encoding="utf-8")

            files = collect_input_files(str(root))

        self.assertEqual([path.name for path in files], ["page-1.jpg", "page-2.jpg", "page-10.jpg"])

    def test_collect_input_files_filters_with_regex(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "repair-page-001.jpg").write_bytes(b"1")
            (root / "repair-page-002.jpg").write_bytes(b"2")
            (root / "bank-page-001.jpg").write_bytes(b"bank")

            files = collect_input_files(str(root), r"^repair-page-\d+\.jpg$")

        self.assertEqual(
            [path.name for path in files],
            ["repair-page-001.jpg", "repair-page-002.jpg"],
        )


if __name__ == "__main__":
    unittest.main()