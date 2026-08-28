"""FastAPI application boundary for the reviewable archive UI.

The API deliberately accepts an identity supplied by a trusted reverse proxy.
OIDC and proxy configuration are deployment concerns and are not implemented
here. Direct requests must still provide the identity header, so an anonymous
LAN request cannot query or enumerate the archive.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

import requests

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from archive_manager.ingestion import ingest as ingestion_runtime
from archive_manager.retrieval import query as query_runtime
from archive_manager.security.access_policy import strict_mode
from archive_manager.security.security_config import sensitive_mode
from archive_manager.services.catalog_service import CatalogService
from archive_manager.services.query_service import QueryService
from archive_manager.services.intake_service import IntakeJob, IntakeService
from archive_manager.services.lifecycle_service import LifecycleService
from archive_manager.services.reset_service import ResetService
from archive_manager.lifecycle.retention import expiration_for, is_expired
from archive_manager.paths import PROJECT_ROOT


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=10, ge=1, le=50)
    max_excerpt_chars: int = Field(default=1200, ge=100, le=10000)


class QueryResponse(BaseModel):
    answer: str
    user: str
    route: str = "archive-query"


class PageResponse(BaseModel):
    source_filename: str
    page_number: int | None = None
    page_count: int | None = None


class EventResponse(BaseModel):
    event_id: str
    event_type: str
    pages: list[PageResponse]
    owner: str | None = None
    expires_at: str | None = None
    status: str = "Active"


class ArtifactResponse(BaseModel):
    doc_id: str
    filename: str
    event_id: str | None = None


class DeletePreviewResponse(BaseModel):
    event_id: str
    filenames: list[str]
    document_ids: list[str]
    confirmation_token: str


class ExpiredPreviewResponse(BaseModel):
    event_id: str
    expires_at: str


class PurgePreviewResponse(BaseModel):
    events: list[ExpiredPreviewResponse]
    confirmation_token: str | None = None


class MutationRequest(BaseModel):
    confirmation_token: str = Field(min_length=20, max_length=200)
    confirmation: str = Field(min_length=1, max_length=300)


class ActivityEntry(BaseModel):
    timestamp: str
    kind: str
    status: str
    summary: str
    user: str | None = None


class ResetPreviewResponse(BaseModel):
    actions: list[str]
    confirmation_token: str


class IntakePreviewResponse(BaseModel):
    event_id: str
    event_type: str
    subject_ref: str | None = None
    notes: str | None = None
    pages: list[PageResponse]
    total_bytes: int
    status: str = "validated"


class IntakeSubmissionResponse(BaseModel):
    job_id: str
    event_id: str
    status: str


class IntakeJobResponse(IntakeSubmissionResponse):
    doc_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    failed_files: list[dict[str, str]] = Field(default_factory=list)
    total_files: int = 0
    completed_files: int = 0
    current_file: str | None = None
    updated_at: str = ""


def _user_from_proxy(
    request: Request,
    x_authenticated_user: str | None = Header(default=None),
) -> str:
    """Require a non-empty identity forwarded by the trusted LAN proxy."""
    user = (x_authenticated_user or "").strip()
    local_only = os.environ.get("ARCHIVE_LOCAL_ONLY", "0").lower() in {"1", "true", "yes", "on"}
    if not user and local_only and request.client and request.client.host in {"127.0.0.1", "::1", "localhost"}:
        import getpass

        user = getpass.getuser()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    if len(user) > 255 or "\n" in user or "\r" in user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid authenticated identity",
        )
    if not strict_mode():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ARCHIVE_AUTH_MODE=strict is required for API access",
        )
    return user


def _require_local_reset(request: Request, user: str = Depends(_user_from_proxy)) -> str:
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="Archive reset is available only on this machine")
    if os.environ.get("ARCHIVE_LOCAL_ONLY", "0").lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=503, detail="ARCHIVE_LOCAL_ONLY=1 is required for UI reset")
    return user


def _event_response(event_id: str, manifest: Any) -> EventResponse:
    metadata = manifest.metadata if isinstance(manifest.metadata, dict) else {}
    expiration = expiration_for(manifest)
    return EventResponse(
        event_id=event_id,
        event_type=manifest.event_type,
        pages=[
            PageResponse(
                source_filename=page.source_filename,
                page_number=page.page_number,
                page_count=page.page_count,
            )
            for page in manifest.ordered_pages()
        ],
        owner=metadata.get("owner"),
        expires_at=expiration.isoformat() if expiration else None,
        status="Expired" if is_expired(manifest) else "Active",
    )


def _safe_upload_name(filename: str | None) -> str:
    name = (filename or "").strip()
    safe_name = os.path.basename(name)
    if not safe_name or safe_name in {".", ".."} or len(safe_name) > 255:
        raise HTTPException(status_code=400, detail="Each upload needs a valid filename")
    if os.path.splitext(safe_name)[1].lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(status_code=415, detail=f"Unsupported upload type: {safe_name}")
    return safe_name


def _allowed_users(value: str, user: str) -> list[str]:
    users = sorted({item.strip() for item in value.split(",") if item.strip()} | {user})
    if len(users) > 50:
        raise HTTPException(status_code=422, detail="Too many allowed users")
    return users


def _job_response(job: IntakeJob) -> IntakeJobResponse:
    return IntakeJobResponse(
        job_id=job.job_id,
        event_id=job.event_id,
        status=job.status,
        doc_ids=job.doc_ids,
        error=job.error,
        failed_files=job.failed_files,
        total_files=job.total_files,
        completed_files=job.completed_files,
        current_file=job.current_file,
        updated_at=job.updated_at,
    )


def _activity_entries() -> list[ActivityEntry]:
    entries: list[ActivityEntry] = []
    audit_path = Path(os.environ.get("AUDIT_LOG_PATH", PROJECT_ROOT / "logs" / "query_audit.jsonl"))
    try:
        lines = audit_path.read_text(encoding="utf-8").splitlines()[-50:]
    except OSError:
        lines = []
    for line in lines:
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        outcome_labels = {
            "source_inventory": "Listed indexed source files",
            "total_charge_inventory": "Calculated charge totals",
            "performed_service_inventory": "Listed completed services",
            "service_date_inventory": "Listed service dates",
            "document_date_inventory": "Listed document dates",
            "label_values_inventory": "Listed extracted label values",
            "repair_cause_inventory": "Listed repair causes",
            "service_advisor_inventory": "Listed service advisors",
            "clarification_required": "Query needs clarification",
            "completed": "Completed archive query",
        }
        action = item.get("action")
        outcome = str(item.get("outcome", "completed"))
        entries.append(ActivityEntry(
            timestamp=str(item.get("timestamp", "")),
            kind="mutation" if action else "query",
            status=str(item.get("status", outcome)),
            summary=(f"{action.title()} {len(item.get('event_ids', []))} event(s)"
                     if action else outcome_labels.get(outcome, "Completed archive query")),
            user=str(item.get("user")) if item.get("user") else None,
        ))
    trace_path = Path(os.environ.get("ARCHIVE_TRACE_LOG", PROJECT_ROOT / "logs" / "archive_trace.jsonl"))
    trace_labels = {
        "ingest": "Ingestion completed",
        "normalize_pdf": "Normalized document",
        "pdf_searchability_check": "Checked document searchability",
        "ocr_fast": "OCR completed",
        "ocr": "OCR completed",
        "extract_text": "Parsed document text",
        "embed_chunks": "Embedded document chunks",
        "qdrant_upsert": "Indexed chunks in archive search",
        "event_facts": "Extracted event facts",
    }
    try:
        trace_lines = trace_path.read_text(encoding="utf-8").splitlines()[-100:]
    except OSError:
        trace_lines = []
    for line in trace_lines:
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if item.get("stage") not in trace_labels or item.get("boundary") != "END":
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        source = details.get("source")
        source_name = Path(source).name if source else None
        suffix = f", {source_name}" if source_name else ""
        if item["stage"] == "extract_text" and details.get("page_count"):
            suffix = f" ({details['page_count']} pages{suffix})"
        if item["stage"] == "embed_chunks" and details.get("chunk_count"):
            suffix = f" ({details['chunk_count']} chunks{suffix})"
        entries.append(ActivityEntry(
            timestamp=str(item.get("timestamp", "")),
            kind="ingestion",
            status=str(item.get("status", "completed")),
            summary=trace_labels[item["stage"]] + suffix,
        ))
    return sorted(entries, key=lambda item: item.timestamp, reverse=True)[:50]


def _probe(url: str, headers: dict[str, str] | None = None) -> str:
    try:
        response = requests.get(url, headers=headers or {}, timeout=1.5)
        return "ok" if response.ok else "unhealthy"
    except requests.RequestException:
        return "unreachable"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Archive Manager API",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    catalog_service = CatalogService(query_runtime.EVENT_MANIFEST_PATH)
    query_service = QueryService()
    intake_service = IntakeService(query_runtime.EVENT_MANIFEST_PATH)
    lifecycle_service = LifecycleService(query_runtime.EVENT_MANIFEST_PATH)
    reset_service = ResetService()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "security_mode": "sensitive" if sensitive_mode() else "compatibility",
            "connectivity": "not configured",
        }

    @app.get("/api/ready", status_code=200)
    def readiness() -> dict[str, Any]:
        qdrant_key = os.environ.get("QDRANT_API_KEY", "")
        checks = {
            "qdrant": _probe(
                f"{ingestion_runtime.QDRANT_URL.rstrip('/')}/collections",
                {"api-key": qdrant_key} if qdrant_key else None,
            ),
            "ollama": _probe(f"{ingestion_runtime.OLLAMA_BASE.rstrip('/')}/api/tags"),
            "paddleocr": _probe(f"{os.environ.get('PADDLEOCR_SERVICE_URL', 'http://localhost:8000').rstrip('/')}/health"),
        }
        ready = all(value == "ok" for value in checks.values())
        payload = {"status": "ready" if ready else "degraded", "checks": checks}
        if not ready:
            raise HTTPException(status_code=503, detail=payload)
        return payload

    @app.get("/api/v1/session")
    def session(user: str = Depends(_user_from_proxy)) -> dict[str, str]:
        return {"user": user, "auth": "reverse-proxy-identity"}

    @app.get("/api/v1/security/status")
    def security_status(user: str = Depends(_user_from_proxy)) -> dict[str, Any]:
        return {
            "user": user,
            "security_mode": "sensitive" if sensitive_mode() else "compatibility",
            "authorization_mode": "strict" if strict_mode() else "compatibility",
            "encryption_key_loaded": bool(os.environ.get("ARCHIVE_ENCRYPTION_KEY", "").strip()),
            "qdrant_key_configured": bool(os.environ.get("QDRANT_API_KEY", "").strip()),
            "trace_content_redacted": os.environ.get("ARCHIVE_TRACE_CONTENT", "0").lower() not in {"1", "true", "yes", "on"},
        }

    @app.post("/api/v1/admin/reset-preview", response_model=ResetPreviewResponse)
    def reset_preview(user: str = Depends(_require_local_reset)) -> ResetPreviewResponse:
        del user
        return ResetPreviewResponse(**reset_service.preview())

    @app.post("/api/v1/admin/reset")
    def reset(payload: MutationRequest, user: str = Depends(_require_local_reset)) -> dict[str, str]:
        del user
        try:
            return reset_service.execute(payload.confirmation_token, payload.confirmation)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/activity", response_model=list[ActivityEntry])
    def activity(user: str = Depends(_user_from_proxy)) -> list[ActivityEntry]:
        entries = _activity_entries()
        entries.extend(
            ActivityEntry(
                kind="ingestion",
                status=job.status,
                summary=(f"Ingested {job.completed_files}/{job.total_files} files for event {job.event_id}"
                         + (f" ({job.current_file})" if job.current_file else "")),
                timestamp=job.updated_at,
                user=user,
            )
            for job in intake_service.list(user)
        )
        entries.extend(
            ActivityEntry(
                timestamp=job.updated_at if hasattr(job, "updated_at") else "",
                kind="mutation",
                status=job.status,
                summary=f"{job.action} {len(job.event_ids)} event(s)",
                user=user,
            )
            for job in lifecycle_service.list_jobs(user)
        )
        return entries[:50]

    @app.get("/api/v1/events", response_model=list[EventResponse])
    def list_events(
        user: str = Depends(_user_from_proxy),
        search: str = Query(default="", max_length=200),
    ) -> list[EventResponse]:
        normalized_search = search.casefold().strip()
        manifests = catalog_service.visible_manifests(user)
        return [
            _event_response(event_id, manifest)
            for event_id, manifest in sorted(manifests.items())
            if not normalized_search
            or normalized_search in event_id.casefold()
            or normalized_search in manifest.event_type.casefold()
        ]

    @app.get("/api/v1/artifacts", response_model=list[ArtifactResponse])
    def list_artifacts(user: str = Depends(_user_from_proxy)) -> list[ArtifactResponse]:
        return [ArtifactResponse(**artifact) for artifact in catalog_service.visible_artifacts(user)]

    @app.get("/api/v1/events/expired-preview", response_model=list[ExpiredPreviewResponse])
    def expired_preview(user: str = Depends(_user_from_proxy)) -> list[ExpiredPreviewResponse]:
        return [ExpiredPreviewResponse(**item) for item in lifecycle_service.expired_preview(user)]

    @app.get("/api/v1/events/purge-preview", response_model=PurgePreviewResponse)
    def purge_preview(user: str = Depends(_user_from_proxy)) -> PurgePreviewResponse:
        return PurgePreviewResponse(**lifecycle_service.purge_preview(user))

    @app.get("/api/v1/events/{event_id}", response_model=EventResponse)
    def get_event(event_id: str, user: str = Depends(_user_from_proxy)) -> EventResponse:
        manifest = catalog_service.visible_manifests(user).get(event_id)
        if manifest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
        return _event_response(event_id, manifest)

    @app.post("/api/v1/events/{event_id}/delete-preview", response_model=DeletePreviewResponse)
    def delete_preview(event_id: str, user: str = Depends(_user_from_proxy)) -> DeletePreviewResponse:
        try:
            return lifecycle_service.delete_preview(event_id, user)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/api/v1/events/mutation", status_code=202)
    def execute_mutation(payload: MutationRequest, user: str = Depends(_user_from_proxy)) -> dict:
        try:
            return lifecycle_service.execute(payload.confirmation_token, payload.confirmation, user)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/events/mutation-jobs/{job_id}")
    def mutation_job(job_id: str, user: str = Depends(_user_from_proxy)) -> dict:
        job = lifecycle_service.get_job(job_id, user)
        if job is None:
            raise HTTPException(status_code=404, detail="Mutation job not found")
        return job.to_dict()

    @app.post("/api/v1/intake/preview", response_model=IntakePreviewResponse)
    async def preview_intake(
        files: list[UploadFile] = File(..., min_length=1, max_length=100),
        event_id: str = Form(..., min_length=1, max_length=200),
        event_type: str = Form(default="general_document"),
        subject_ref: str | None = Form(default=None, max_length=255),
        notes: str | None = Form(default=None, max_length=1000),
        user: str = Depends(_user_from_proxy),
    ) -> IntakePreviewResponse:
        del user
        if event_type not in {"automotive_service", "tax", "medical", "insurance", "investment", "retirement", "banking_statement", "general_document"}:
            raise HTTPException(status_code=422, detail="Unsupported event type")
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise HTTPException(status_code=422, detail="event_id must not be empty")

        page_responses = []
        total_bytes = 0
        for index, upload in enumerate(files, start=1):
            filename = _safe_upload_name(upload.filename)
            content = await upload.read(50 * 1024 * 1024 + 1)
            if len(content) > 50 * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"Upload is too large: {filename}")
            total_bytes += len(content)
            page_responses.append(PageResponse(
                source_filename=filename,
                page_number=index,
                page_count=len(files),
            ))
        return IntakePreviewResponse(
            event_id=normalized_event_id,
            event_type=event_type,
            subject_ref=subject_ref.strip() if subject_ref else None,
            notes=notes.strip() if notes else None,
            pages=page_responses,
            total_bytes=total_bytes,
        )

    @app.post("/api/v1/intake", response_model=IntakeSubmissionResponse, status_code=202)
    async def submit_intake(
        files: list[UploadFile] = File(..., min_length=1, max_length=100),
        event_id: str = Form(..., min_length=1, max_length=200),
        event_type: str = Form(default="general_document"),
        subject_ref: str | None = Form(default=None, max_length=255),
        notes: str | None = Form(default=None, max_length=1000),
        allowed_users: str = Form(default=""),
        user: str = Depends(_user_from_proxy),
    ) -> IntakeSubmissionResponse:
        normalized_files = []
        for upload in files:
            filename = _safe_upload_name(upload.filename)
            content = await upload.read(50 * 1024 * 1024 + 1)
            if len(content) > 50 * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"Upload is too large: {filename}")
            normalized_files.append((filename, content))
        try:
            job = intake_service.submit(
                event_id=event_id.strip(),
                event_type=event_type,
                subject_ref=subject_ref.strip() if subject_ref else None,
                notes=notes.strip() if notes else None,
                allowed_users=_allowed_users(allowed_users, user),
                files=normalized_files,
                user=user,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _job_response(job)

    @app.get("/api/v1/intake/jobs/{job_id}", response_model=IntakeJobResponse)
    def intake_job(job_id: str, user: str = Depends(_user_from_proxy)) -> IntakeJobResponse:
        job = intake_service.get(job_id, user)
        if job is None:
            raise HTTPException(status_code=404, detail="Intake job not found")
        return _job_response(job)

    @app.post("/api/v1/query", response_model=QueryResponse)
    def answer_query(payload: QueryRequest, user: str = Depends(_user_from_proxy)) -> QueryResponse:
        try:
            answer = query_service.answer(
                payload.question,
                user=user,
                top_k=payload.top_k,
                max_excerpt_chars=payload.max_excerpt_chars,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return QueryResponse(answer=str(answer), user=user)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "archive_manager.api.app:app",
        host=os.environ.get("ARCHIVE_API_HOST", "127.0.0.1"),
        port=int(os.environ.get("ARCHIVE_API_PORT", "8080")),
        reload=False,
    )
