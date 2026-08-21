import unittest
from datetime import datetime, timezone

from archive_manager.core.event_model import EventManifest
from archive_manager.admin.purge_expired import expired_event_ids
from archive_manager.lifecycle.retention import expiration_for, is_expired


class RetentionTest(unittest.TestCase):
    def test_retention_days_requires_created_at(self):
        manifest = EventManifest(event_id="event-1", metadata={"retention_days": 30})

        self.assertIsNone(expiration_for(manifest))
        self.assertFalse(is_expired(manifest))

    def test_explicit_expiration_is_detected(self):
        manifest = EventManifest(
            event_id="event-1",
            metadata={"expires_at": "2020-01-01T00:00:00Z"},
        )

        self.assertTrue(is_expired(manifest, datetime(2020, 1, 2, tzinfo=timezone.utc)))

    def test_expired_event_ids_are_sorted_and_skip_unmanaged_events(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from archive_manager.core.event_manifests import save_manifests

        manifests = {
            "event-b": EventManifest(
                event_id="event-b", metadata={"expires_at": "2020-01-01T00:00:00Z"}
            ),
            "event-a": EventManifest(event_id="event-a"),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.json"
            save_manifests(path, manifests)
            with patch("archive_manager.admin.purge_expired.MANIFEST_PATH", path):
                self.assertEqual(expired_event_ids(datetime(2020, 2, 1, tzinfo=timezone.utc)), ["event-b"])


if __name__ == "__main__":
    unittest.main()