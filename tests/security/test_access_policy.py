import os
import unittest
from unittest.mock import patch

from archive_manager.security.access_policy import authorized_event_ids, is_authorized, is_source_authorized
from archive_manager.core.event_model import EventManifest


class AccessPolicyTest(unittest.TestCase):
    def test_compatibility_mode_allows_existing_manifest(self):
        manifest = EventManifest(event_id="event-1")
        with patch.dict(os.environ, {"ARCHIVE_AUTH_MODE": "compat"}, clear=False):
            self.assertTrue(is_authorized(manifest))

    def test_strict_mode_requires_allowed_user(self):
        manifest = EventManifest(
            event_id="event-1",
            metadata={"allowed_users": ["alice"]},
        )
        with patch.dict(os.environ, {"ARCHIVE_AUTH_MODE": "strict", "ARCHIVE_AUDIT_USER": "bob"}, clear=False):
            self.assertFalse(is_authorized(manifest))
            self.assertEqual(authorized_event_ids({"event-1": manifest}), set())

        with patch.dict(os.environ, {"ARCHIVE_AUTH_MODE": "strict", "ARCHIVE_AUDIT_USER": "alice"}, clear=False):
            self.assertTrue(is_authorized(manifest))

    def test_strict_mode_denies_unmanifested_sources(self):
        with patch.dict(os.environ, {"ARCHIVE_AUTH_MODE": "strict"}, clear=False):
            self.assertFalse(is_source_authorized(None))

        with patch.dict(os.environ, {"ARCHIVE_AUTH_MODE": "compat"}, clear=False):
            self.assertTrue(is_source_authorized(None))


if __name__ == "__main__":
    unittest.main()