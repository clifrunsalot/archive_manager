"""Background intake jobs for authenticated API clients."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from pathlib import Path

from archive_manager.core.event_manifests import load_manifests, save_manifests
from archive_manager.ingestion.ingest import ARCHIVE_DIR, ingest_pdf
from archive_manager.ingestion.intake import create_manifest
from archive_manager.paths import PROJECT_ROOT


@dataclass
class IntakeJob:
    job_id: str
    event_id: str
    user: str
    status: str = "queued"
    doc_ids: list[str] = field(default_factory=list)
    error: str | None = None
    failed_files: list[dict[str, str]] = field(default_factory=list)
    staged_paths: list[str] = field(default_factory=list)
    total_files: int = 0
    completed_files: int = 0
    current_file: str | None = None
    updated_at: str = ""


class IntakeService:
    """Persist one manifest and process its pages without blocking HTTP."""

    def __init__(self, manifest_path, archive_dir: Path = ARCHIVE_DIR, workers: int = 2, jobs_path: Path | None = None):
        self.manifest_path = manifest_path
        self.archive_dir = Path(archive_dir)
        self.jobs_path = Path(jobs_path or PROJECT_ROOT / "data" / ".api_jobs" / "jobs.json")
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="archive-intake")
        self._lock = threading.Lock()
        self._jobs = self._load_jobs()

    def _load_jobs(self) -> dict[str, IntakeJob]:
        try:
            raw_jobs = json.loads(self.jobs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw_jobs, dict):
            return {}
        jobs = {}
        for job_id, raw_job in raw_jobs.items():
            if isinstance(raw_job, dict):
                raw_job["job_id"] = job_id
                jobs[job_id] = IntakeJob(**{key: raw_job[key] for key in IntakeJob.__dataclass_fields__ if key in raw_job})
        return jobs

    def _save_jobs(self) -> None:
        self.jobs_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.jobs_path.parent.chmod(0o700)
        temporary_path = self.jobs_path.with_suffix(".tmp")
        payload = {job_id: job.__dict__ for job_id, job in self._jobs.items()}
        temporary_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary_path.chmod(0o600)
        temporary_path.replace(self.jobs_path)

    def submit(
        self,
        event_id: str,
        event_type: str,
        subject_ref: str | None,
        notes: str | None,
        allowed_users: list[str],
        files: list[tuple[str, bytes]],
        user: str,
    ) -> IntakeJob:
        manifests = load_manifests(self.manifest_path)
        if event_id in manifests:
            raise ValueError(f"Event ID already exists: {event_id}")
        if not files:
            raise ValueError("At least one file is required")

        self.archive_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.archive_dir.chmod(0o700)
        event_dir = self.archive_dir / f".api-{uuid.uuid4().hex}"
        event_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        staged_paths = []
        try:
            for filename, content in files:
                path = event_dir / filename
                path.write_bytes(content)
                path.chmod(0o600)
                staged_paths.append(path)
            manifest = create_manifest(
                event_id,
                event_type,
                [filename for filename, _content in files],
                subject_ref=subject_ref,
                metadata={
                    "allowed_users": sorted(set(allowed_users)),
                    **({"notes": notes} if notes else {}),
                },
            )
            save_manifests(self.manifest_path, {**manifests, event_id: manifest})
        except Exception:
            for path in staged_paths:
                path.unlink(missing_ok=True)
            event_dir.rmdir()
            raise

        job = IntakeJob(
            job_id=uuid.uuid4().hex, event_id=event_id, user=user,
            total_files=len(staged_paths),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        job.staged_paths = [str(path) for path in staged_paths]
        with self._lock:
            self._jobs[job.job_id] = job
            self._save_jobs()
        self._executor.submit(self._run, job.job_id, staged_paths)
        return job

    def get(self, job_id: str, user: str) -> IntakeJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.user != user:
                return None
            return IntakeJob(**job.__dict__)

    def list(self, user: str) -> list[IntakeJob]:
        with self._lock:
            return [IntakeJob(**job.__dict__) for job in self._jobs.values() if job.user == user]

    def _run(self, job_id: str, staged_paths: list[Path]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.updated_at = datetime.now(timezone.utc).isoformat()
            self._save_jobs()
        try:
            doc_ids = []
            for path in staged_paths:
                with self._lock:
                    job.current_file = path.name
                    job.updated_at = datetime.now(timezone.utc).isoformat()
                    self._save_jobs()
                try:
                    doc_ids.append(ingest_pdf(path, source_filename=path.name))
                    with self._lock:
                        job.completed_files += 1
                        job.updated_at = datetime.now(timezone.utc).isoformat()
                        self._save_jobs()
                except Exception as exc:
                    with self._lock:
                        job.failed_files.append({"filename": path.name, "error": str(exc)})
                        job.updated_at = datetime.now(timezone.utc).isoformat()
                        self._save_jobs()
            with self._lock:
                job.doc_ids = doc_ids
                job.status = "completed_with_errors" if job.failed_files else "completed"
                job.error = (
                    f"{len(job.failed_files)} file(s) failed during ingestion"
                    if job.failed_files else None
                )
                job.staged_paths = []
                job.current_file = None
                job.updated_at = datetime.now(timezone.utc).isoformat()
                self._save_jobs()
        except Exception as exc:
            with self._lock:
                job.status = "failed"
                job.error = str(exc)
                job.updated_at = datetime.now(timezone.utc).isoformat()
                self._save_jobs()
