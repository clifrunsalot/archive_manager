import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_manager.retrieval.query as query
from archive_manager.core.event_manifests import save_manifests
from archive_manager.core.event_model import EventManifest, PageMetadata


class QueryDomainIntegrationTest(unittest.TestCase):
    def test_manifest_domain_parser_facts_are_returned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            searchable = root / "searchable"
            searchable.mkdir()
            (searchable / "doc-1.txt").write_text(
                "Statement Period: Jan 01 - Jan 31, 2026\nAccount Type: Checking",
                encoding="utf-8",
            )
            manifest = EventManifest(
                event_id="bank-event",
                event_type="banking_statement",
                pages=[PageMetadata("statement.jpg", 1, 1)],
            )
            manifest_path = root / "events.json"
            save_manifests(manifest_path, {manifest.event_id: manifest})
            with (
                patch.object(query, "SEARCHABLE_DIR", searchable),
                patch.object(query, "EVENT_MANIFEST_PATH", manifest_path),
                patch.object(query, "load_ingest_cache", return_value={"doc-1": "statement.jpg"}),
            ):
                facts = query._record_facts(["statement.jpg"])

        self.assertEqual(facts["domain"], "banking_statement")
        self.assertEqual(facts["fields"][0]["name"], "statement_period")
        self.assertEqual(facts["fields"][0]["page"], 1)


if __name__ == "__main__":
    unittest.main()