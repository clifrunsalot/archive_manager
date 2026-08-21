import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archive_manager.lifecycle.audit import record_query_audit


class AuditTest(unittest.TestCase):
    def test_audit_log_hashes_question_and_excludes_raw_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            with patch("archive_manager.lifecycle.audit.AUDIT_LOG_PATH", path):
                record_query_audit(
                    "What is the medical diagnosis for Jane?",
                    hit_count=2,
                    event_ids=["event-2", "event-1", "event-1"],
                )

            entry = json.loads(path.read_text(encoding="utf-8"))

        self.assertNotIn("medical diagnosis", json.dumps(entry))
        self.assertEqual(entry["event_ids"], ["event-1", "event-2"])
        self.assertEqual(len(entry["question_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()