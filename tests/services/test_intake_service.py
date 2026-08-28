import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archive_manager.services.intake_service import IntakeService


class IntakeServiceTest(unittest.TestCase):
    def test_failed_file_is_recorded_and_later_files_continue(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            service = IntakeService(
                root / "events.json",
                archive_dir=root / "archive",
                jobs_path=root / "jobs.json",
            )
            with patch("archive_manager.services.intake_service.load_manifests", return_value={}), \
                 patch("archive_manager.services.intake_service.save_manifests"), \
                 patch(
                     "archive_manager.services.intake_service.ingest_pdf",
                     side_effect=["doc-1", RuntimeError("Qdrant unavailable"), "doc-3"],
                 ):
                job = service.submit(
                    "event-1",
                    "general_document",
                    None,
                    None,
                    ["alice"],
                    [("first.pdf", b"one"), ("broken.pdf", b"two"), ("last.pdf", b"three")],
                    "alice",
                )
                service._executor.shutdown(wait=True)

            result = service.get(job.job_id, "alice")
            self.assertIsNotNone(result)
            self.assertEqual(result.status, "completed_with_errors")
            self.assertEqual(result.completed_files, 2)
            self.assertEqual(result.doc_ids, ["doc-1", "doc-3"])
            self.assertEqual(result.failed_files, [{"filename": "broken.pdf", "error": "Qdrant unavailable"}])


if __name__ == "__main__":
    unittest.main()
