import unittest
import os
import requests
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from archive_manager.api.app import create_app
from archive_manager.core.event_model import EventManifest, PageMetadata


class ArchiveApiTest(unittest.TestCase):
    def setUp(self):
        self.auth_mode = patch.dict(os.environ, {"ARCHIVE_AUTH_MODE": "strict"})
        self.auth_mode.start()
        self.addCleanup(self.auth_mode.stop)
        self.client = TestClient(create_app(), client=("127.0.0.1", 8000))
        self.manifests = {
            "private-event": EventManifest(
                event_id="private-event",
                event_type="medical",
                pages=[PageMetadata("visit.pdf", 1, 1)],
                metadata={"allowed_users": ["alice"]},
            ),
            "shared-event": EventManifest(
                event_id="shared-event",
                event_type="tax",
                pages=[PageMetadata("tax.pdf", 1, 1)],
                metadata={"allowed_users": ["alice", "bob"]},
            ),
        }

    def test_health_does_not_require_login(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("archive_manager.api.app.requests.get")
    def test_readiness_reports_all_dependencies(self, get):
        get.return_value.ok = True
        response = self.client.get("/api/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "status": "ready",
            "checks": {"qdrant": "ok", "ollama": "ok", "paddleocr": "ok"},
        })

    @patch("archive_manager.api.app.requests.get", side_effect=requests.ConnectionError("offline"))
    def test_readiness_returns_degraded_without_leaking_urls(self, get):
        response = self.client.get("/api/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["status"], "degraded")
        self.assertNotIn("localhost", str(response.json()))

    def test_archive_routes_require_proxy_identity(self):
        response = self.client.get("/api/v1/events")
        self.assertEqual(response.status_code, 401)

    def test_local_only_mode_uses_os_identity_for_loopback(self):
        with patch.dict(os.environ, {"ARCHIVE_LOCAL_ONLY": "1"}, clear=False), patch(
            "getpass.getuser", return_value="cliftonhudson"
        ):
            response = self.client.get("/api/v1/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"], "cliftonhudson")

    def test_security_status_contains_no_secret_values(self):
        response = self.client.get(
            "/api/v1/security/status", headers={"X-Authenticated-User": "alice"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"], "alice")
        self.assertNotIn("key", response.json()["security_mode"])

    @patch("archive_manager.api.app.ResetService.preview")
    def test_reset_preview_is_available_only_in_local_mode(self, preview):
        preview.return_value = {"actions": ["Clear archive"], "confirmation_token": "token-token-token-token"}
        with patch.dict(os.environ, {"ARCHIVE_LOCAL_ONLY": "1"}, clear=False):
            response = self.client.post("/api/v1/admin/reset-preview", headers={"X-Authenticated-User": "alice"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["actions"], ["Clear archive"])

    @patch("archive_manager.api.app._activity_entries", return_value=[])
    @patch("archive_manager.api.app.IntakeService.list", return_value=[])
    @patch("archive_manager.api.app.LifecycleService.list_jobs", return_value=[])
    def test_activity_requires_identity_and_returns_sanitized_entries(self, list_jobs, list_intake, entries):
        response = self.client.get(
            "/api/v1/activity", headers={"X-Authenticated-User": "alice"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    @patch("archive_manager.api.app.PROJECT_ROOT")
    def test_activity_uses_human_readable_query_summary(self, project_root):
        import json
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            project_root.__truediv__.return_value = Path(tmpdir)
            audit_path = Path(tmpdir) / "query_audit.jsonl"
            audit_path.write_text(json.dumps({
                "timestamp": "2026-08-27T12:00:00+00:00",
                "outcome": "source_inventory",
                "user": "alice",
            }) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"AUDIT_LOG_PATH": str(audit_path)}, clear=False):
                entries = __import__("archive_manager.api.app", fromlist=["_activity_entries"])._activity_entries()
        self.assertEqual(entries[0].summary, "Listed indexed source files")

    def test_activity_includes_timestamped_ingestion_phases(self):
        import json
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "archive_trace.jsonl"
            trace_path.write_text(json.dumps({
                "timestamp": "2026-08-27T12:00:00+00:00",
                "stage": "embed_chunks",
                "boundary": "END",
                "status": "completed",
                "details": {"chunk_count": 12, "source": "lesson.pdf"},
            }) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"ARCHIVE_TRACE_LOG": str(trace_path)}, clear=False):
                entries = __import__("archive_manager.api.app", fromlist=["_activity_entries"])._activity_entries()
        self.assertIn("Embedded document chunks (12 chunks, lesson.pdf)", {entry.summary for entry in entries})

    @patch("archive_manager.services.catalog_service.load_manifests")
    def test_events_are_filtered_by_authenticated_user(self, load_manifests):
        load_manifests.return_value = self.manifests
        response = self.client.get(
            "/api/v1/events", headers={"X-Authenticated-User": "bob"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([event["event_id"] for event in response.json()], ["shared-event"])

    @patch("archive_manager.services.catalog_service.load_ingest_cache")
    @patch("archive_manager.services.catalog_service.load_manifests")
    def test_artifacts_are_listed_for_authenticated_user(self, load_manifests, load_cache):
        load_manifests.return_value = self.manifests
        load_cache.return_value = {"doc-1": "tax.pdf", "doc-2": "private.pdf"}
        response = self.client.get("/api/v1/artifacts", headers={"X-Authenticated-User": "bob"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["filename"] for item in response.json()], ["tax.pdf"])

    @patch("archive_manager.services.catalog_service.load_ingest_cache", return_value={})
    @patch("archive_manager.services.catalog_service.load_manifests", return_value={})
    def test_artifact_list_is_empty_after_reset(self, load_manifests, load_cache):
        response = self.client.get("/api/v1/artifacts", headers={"X-Authenticated-User": "alice"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    @patch("archive_manager.services.catalog_service.load_manifests")
    def test_event_detail_does_not_leak_unauthorized_event(self, load_manifests):
        load_manifests.return_value = self.manifests
        response = self.client.get(
            "/api/v1/events/private-event", headers={"X-Authenticated-User": "bob"}
        )
        self.assertEqual(response.status_code, 404)

    def test_intake_preview_validates_and_orders_uploads(self):
        response = self.client.post(
            "/api/v1/intake/preview",
            headers={"X-Authenticated-User": "alice"},
            data={"event_id": "new-event", "event_type": "medical"},
            files=[
                ("files", ("page-2.jpg", b"second", "image/jpeg")),
                ("files", ("page-1.jpg", b"first", "image/jpeg")),
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([page["page_number"] for page in response.json()["pages"]], [1, 2])
        self.assertEqual(response.json()["total_bytes"], 11)

    def test_intake_preview_rejects_unsupported_uploads(self):
        response = self.client.post(
            "/api/v1/intake/preview",
            headers={"X-Authenticated-User": "alice"},
            data={"event_id": "new-event"},
            files={"files": ("notes.txt", b"private", "text/plain")},
        )
        self.assertEqual(response.status_code, 415)

    def test_intake_preview_accepts_more_than_twenty_pages(self):
        response = self.client.post(
            "/api/v1/intake/preview",
            headers={"X-Authenticated-User": "alice"},
            data={"event_id": "large-event", "event_type": "medical"},
            files=[("files", (f"page-{index}.jpg", b"page", "image/jpeg")) for index in range(21)],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["pages"]), 21)

    @patch("archive_manager.api.app.IntakeService.submit")
    def test_intake_submission_returns_background_job(self, submit):
        from archive_manager.services.intake_service import IntakeJob

        submit.return_value = IntakeJob("job-1", "new-event", "alice")
        response = self.client.post(
            "/api/v1/intake",
            headers={"X-Authenticated-User": "alice"},
            data={"event_id": "new-event", "event_type": "medical"},
            files={"files": ("visit.jpg", b"page", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")

    @patch("archive_manager.services.catalog_service.load_manifests")
    def test_catalog_exposes_expiration_status(self, load_manifests):
        from datetime import datetime, timezone

        expired = EventManifest(
            event_id="expired-event",
            event_type="tax",
            metadata={"expires_at": "2020-01-01T00:00:00+00:00", "allowed_users": ["alice"]},
        )
        load_manifests.return_value = {"expired-event": expired}
        response = self.client.get(
            "/api/v1/events", headers={"X-Authenticated-User": "alice"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["status"], "Expired")

    @patch("archive_manager.api.app.LifecycleService.delete_preview")
    def test_delete_preview_is_authenticated(self, delete_preview):
        delete_preview.return_value = {
            "event_id": "shared-event",
            "filenames": ["tax.pdf"],
            "document_ids": ["abc"],
            "confirmation_token": "token-token-token-token",
        }
        response = self.client.post(
            "/api/v1/events/shared-event/delete-preview",
            headers={"X-Authenticated-User": "alice"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["document_ids"], ["abc"])
        self.assertEqual(response.json()["confirmation_token"], "token-token-token-token")

    @patch("archive_manager.api.app.LifecycleService.purge_preview")
    def test_purge_preview_returns_confirmation_token(self, purge_preview):
        purge_preview.return_value = {
            "events": [{"event_id": "old-event", "expires_at": "2020-01-01T00:00:00+00:00"}],
            "confirmation_token": "token-token-token-token",
        }
        response = self.client.get(
            "/api/v1/events/purge-preview",
            headers={"X-Authenticated-User": "alice"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"][0]["event_id"], "old-event")

    @patch("archive_manager.api.app.LifecycleService.execute")
    def test_mutation_returns_background_job(self, execute):
        execute.return_value = {"job_id": "job-1", "user": "alice", "action": "delete", "event_ids": ["shared-event"], "status": "queued", "error": None}
        response = self.client.post(
            "/api/v1/events/mutation",
            headers={"X-Authenticated-User": "alice"},
            json={"confirmation_token": "token-token-token-token", "confirmation": "DELETE shared-event"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")


if __name__ == "__main__":
    unittest.main()
