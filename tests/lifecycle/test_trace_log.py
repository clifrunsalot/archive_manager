import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archive_manager.lifecycle.trace_log import new_run_id, trace_event


class TraceLogTest(unittest.TestCase):
    def test_trace_event_writes_correlated_stage_boundary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "archive_trace.jsonl"
            with patch("archive_manager.lifecycle.trace_log.TRACE_LOG_PATH", path):
                run_id = new_run_id("query")
                trace_event(run_id, "llm_input", "BEGIN", question="What?", extracted=["fact"])
                trace_event(run_id, "answer", "END", status="completed", answer="Done")

            entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([entry["run_id"] for entry in entries], [run_id, run_id])
        self.assertEqual([entry["boundary"] for entry in entries], ["BEGIN", "END"])
        self.assertEqual(entries[0]["details"]["extracted"], ["fact"])
        self.assertEqual(entries[1]["details"]["answer"], "Done")


if __name__ == "__main__":
    unittest.main()
