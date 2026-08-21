import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_manager.admin.reset_archive as reset_archive


class ResetArchiveTest(unittest.TestCase):
    def test_reset_removes_event_manifest_and_facts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "events.json"
            facts = root / "facts.json"
            manifest.write_text("{}", encoding="utf-8")
            facts.write_text("{}", encoding="utf-8")
            with (
                patch.object(reset_archive, "EVENT_MANIFEST_FILE", manifest),
                patch.object(reset_archive, "EVENT_FACTS_FILE", facts),
                patch.object(reset_archive, "delete_qdrant_collection"),
                patch.object(reset_archive, "clear_ingest_cache"),
                patch.object(reset_archive, "clear_directory_contents"),
                patch.object(reset_archive, "confirm_or_exit"),
                patch("sys.argv", ["reset_archive.py", "--force"]),
            ):
                result = reset_archive.main()

        self.assertEqual(result, 0)
        self.assertFalse(manifest.exists())
        self.assertFalse(facts.exists())

    def test_reset_removes_generated_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs = Path(tmpdir) / "logs"
            logs.mkdir()
            (logs / "ingest_example.log").write_text("log", encoding="utf-8")
            with (
                patch.object(reset_archive, "LOGS_DIR", logs),
                patch.object(reset_archive, "delete_qdrant_collection"),
                patch.object(reset_archive, "clear_ingest_cache"),
                patch.object(reset_archive, "clear_directory_contents") as clear_dir,
                patch.object(reset_archive, "confirm_or_exit"),
                patch("sys.argv", ["reset_archive.py", "--force"]),
            ):
                result = reset_archive.main()

        self.assertEqual(result, 0)
        self.assertIn((logs,), [call.args for call in clear_dir.call_args_list])


if __name__ == "__main__":
    unittest.main()