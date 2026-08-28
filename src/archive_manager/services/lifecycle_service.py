"""Read-only lifecycle previews for authenticated API clients."""

from __future__ import annotations

from datetime import datetime, timezone
import secrets
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from archive_manager.admin import delete_event
from archive_manager.admin.purge_expired import expired_event_ids
from archive_manager.core.event_manifests import load_manifests
from archive_manager.lifecycle.retention import expiration_for, is_expired
from archive_manager.security.access_policy import as_user, is_authorized
from archive_manager.lifecycle.mutation_audit import record_mutation_audit
from archive_manager.paths import PROJECT_ROOT


@dataclass
class _Confirmation:
    user: str
    action: str
    event_ids: tuple[str, ...]
    expires_at: float

    def to_dict(self) -> dict:
        return {"user": self.user, "action": self.action, "event_ids": list(self.event_ids), "expires_at": self.expires_at}


@dataclass
class MutationJob:
    job_id: str
    user: str
    action: str
    event_ids: list[str]
    status: str = "queued"
    error: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class LifecycleService:
    """Expose deletion and retention impact without mutating the archive."""

    def __init__(self, manifest_path, confirmation_path: Path | None = None):
        self.manifest_path = manifest_path
        self.confirmation_path = Path(confirmation_path or PROJECT_ROOT / "data" / ".api_jobs" / "confirmations.json")
        self._lock = threading.Lock()
        self._confirmations = self._load_confirmations()
        self._jobs_path = self.confirmation_path.with_name("mutation_jobs.json")
        self._jobs: dict[str, MutationJob] = self._load_jobs()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="archive-mutation")

    def _load_jobs(self) -> dict[str, MutationJob]:
        try:
            data = json.loads(self._jobs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            job_id: MutationJob(job_id=job_id, user=value["user"], action=value["action"], event_ids=value["event_ids"], status=value.get("status", "queued"), error=value.get("error"))
            for job_id, value in data.items() if isinstance(value, dict)
        }

    def _save_jobs(self) -> None:
        self._jobs_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._jobs_path.parent.chmod(0o700)
        temporary_path = self._jobs_path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps({job_id: job.to_dict() for job_id, job in self._jobs.items()}, sort_keys=True), encoding="utf-8")
        temporary_path.chmod(0o600)
        temporary_path.replace(self._jobs_path)

    def _load_confirmations(self) -> dict[str, _Confirmation]:
        try:
            data = json.loads(self.confirmation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        now = datetime.now(timezone.utc).timestamp()
        return {
            token: _Confirmation(
                user=value["user"], action=value["action"],
                event_ids=tuple(value["event_ids"]), expires_at=float(value["expires_at"]),
            )
            for token, value in data.items()
            if isinstance(value, dict) and value.get("expires_at", 0) > now
        }

    def _save_confirmations(self) -> None:
        self.confirmation_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.confirmation_path.parent.chmod(0o700)
        temporary_path = self.confirmation_path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps({token: item.to_dict() for token, item in self._confirmations.items()}, sort_keys=True), encoding="utf-8")
        temporary_path.chmod(0o600)
        temporary_path.replace(self.confirmation_path)

    def delete_preview(self, event_id: str, user: str) -> dict:
        with as_user(user):
            original_path = delete_event.MANIFEST_PATH
            delete_event.MANIFEST_PATH = self.manifest_path
            try:
                preview = delete_event.delete_event(event_id, dry_run=True)
                preview["confirmation_token"] = self._issue_confirmation(user, "delete", (event_id,))
                return preview
            finally:
                delete_event.MANIFEST_PATH = original_path

    def expired_preview(self, user: str) -> list[dict]:
        manifests = load_manifests(self.manifest_path)
        with as_user(user):
            original_path = delete_event.MANIFEST_PATH
            delete_event.MANIFEST_PATH = self.manifest_path
            try:
                event_ids = expired_event_ids(datetime.now(timezone.utc))
            finally:
                delete_event.MANIFEST_PATH = original_path
        return [
            {
                "event_id": event_id,
                "expires_at": expiration_for(manifests[event_id]).isoformat(),
            }
            for event_id in event_ids
            if event_id in manifests and is_authorized(manifests[event_id], user)
        ]

    def _issue_confirmation(self, user: str, action: str, event_ids: tuple[str, ...]) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._confirmations[token] = _Confirmation(
                user=user, action=action, event_ids=event_ids,
                expires_at=datetime.now(timezone.utc).timestamp() + 300,
            )
            self._save_confirmations()
        return token

    def purge_preview(self, user: str) -> dict:
        items = self.expired_preview(user)
        event_ids = tuple(item["event_id"] for item in items)
        return {
            "events": items,
            "confirmation_token": self._issue_confirmation(user, "purge", event_ids) if event_ids else None,
        }

    def execute(self, token: str, confirmation: str, user: str) -> dict:
        with self._lock:
            pending = self._confirmations.get(token)
            if pending is None or pending.user != user or pending.expires_at < datetime.now(timezone.utc).timestamp():
                raise PermissionError("Confirmation token is invalid or expired")
        expected = f"DELETE {pending.event_ids[0]}" if pending.action == "delete" else "PURGE EXPIRED EVENTS"
        if confirmation != expected:
            raise ValueError(f"Type exactly: {expected}")
        with self._lock:
            self._confirmations.pop(token, None)
            self._save_confirmations()
        job = MutationJob(uuid.uuid4().hex, user, pending.action, list(pending.event_ids))
        with self._lock:
            self._jobs[job.job_id] = job
            self._save_jobs()
        self._executor.submit(self._run_mutation, job.job_id)
        return job.to_dict()

    def get_job(self, job_id: str, user: str) -> MutationJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.user != user:
                return None
            return MutationJob(**job.to_dict())

    def list_jobs(self, user: str) -> list[MutationJob]:
        with self._lock:
            return [MutationJob(**job.to_dict()) for job in self._jobs.values() if job.user == user]

    def _run_mutation(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            self._save_jobs()
        record_mutation_audit(job.user, job.action, job.event_ids, status="started")
        results = []
        try:
            with as_user(job.user):
                original_path = delete_event.MANIFEST_PATH
                delete_event.MANIFEST_PATH = self.manifest_path
                try:
                    for event_id in job.event_ids:
                        results.append(delete_event.delete_event(event_id))
                finally:
                    delete_event.MANIFEST_PATH = original_path
        except Exception:
            with self._lock:
                job.status = "failed"
                job.error = "Mutation failed; inspect audit log and archive state"
                self._save_jobs()
            record_mutation_audit(job.user, job.action, job.event_ids, status="failed")
            return
        with self._lock:
            job.status = "completed"
            self._save_jobs()
        record_mutation_audit(job.user, job.action, job.event_ids, status="completed")