import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archive_manager.services.lifecycle_service import LifecycleService


class LifecycleServiceTest(unittest.TestCase):
    def test_confirmation_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "confirmations.json"
            first = LifecycleService(Path(tmpdir) / "events.json", confirmation_path=path)
            token = first._issue_confirmation("alice", "delete", ("event-1",))
            restarted = LifecycleService(Path(tmpdir) / "events.json", confirmation_path=path)
            pending = restarted._confirmations[token]
            self.assertEqual(pending.user, "alice")
            self.assertEqual(pending.event_ids, ("event-1",))

    def test_confirmation_requires_owner_and_exact_phrase(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            service = LifecycleService(Path(tmpdir) / "events.json", confirmation_path=Path(tmpdir) / "confirmations.json")
            token = service._issue_confirmation("alice", "delete", ("event-1",))
            with self.assertRaises(PermissionError):
                service.execute(token, "DELETE event-1", "bob")
            with self.assertRaises(ValueError):
                service.execute(token, "delete event-1", "alice")
            with patch("archive_manager.services.lifecycle_service.delete_event.delete_event", return_value={"event_id": "event-1"}), patch("archive_manager.services.lifecycle_service.record_mutation_audit"), patch.object(service._executor, "submit") as submit:
                result = service.execute(token, "DELETE event-1", "alice")
            self.assertEqual(result["event_ids"], ["event-1"])
            submit.assert_called_once()
            with self.assertRaises(PermissionError):
                service.execute(token, "DELETE event-1", "alice")


if __name__ == "__main__":
    unittest.main()
