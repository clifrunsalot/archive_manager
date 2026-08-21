import tempfile
import unittest
from pathlib import Path

from archive_manager.core.event_facts import extract_event_facts, load_event_facts, save_event_facts


class EventFactsTest(unittest.TestCase):
    def test_automotive_facts_are_extracted_with_services(self):
        facts = extract_event_facts(
            "event-1",
            "automotive_service",
            [{"page": 1, "text": "12SEP24\nPERFORM MULTI POINT INSPECTION\nTOTAL CHARGES\n158.16"}],
        )

        self.assertEqual(facts["event_id"], "event-1")
        self.assertEqual(facts["total_charges"], "158.16")
        self.assertIn("Perform Multi Point Inspection", facts["services_performed"])
        self.assertEqual(facts["source_pages"], [1])

    def test_facts_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "facts.json"
            save_event_facts({"event-1": {"domain": "tax"}}, path)
            self.assertEqual(load_event_facts(path), {"event-1": {"domain": "tax"}})


if __name__ == "__main__":
    unittest.main()