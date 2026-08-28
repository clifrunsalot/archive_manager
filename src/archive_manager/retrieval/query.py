"""Query interface for searching the archive and answering questions from indexed text.

This script embeds the user's question, retrieves the nearest matching document
chunks from Qdrant, and asks an Ollama chat model to answer using only the
retrieved excerpts as context.

When local artifact export is enabled, the script can also save a clean,
standalone Markdown report to a local folder such as artifact_output. This is
opt-in and intentionally limited to local workspace output.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

import archive_manager.config.settings_loader as settings_loader  # noqa: F401
from archive_manager.ingestion.ingest import (
    OLLAMA_BASE,
    SEARCHABLE_DIR,
    load_ingest_cache,
    load_indexed_sources,
    ollama_embed_text,
    qdrant_search,
    qdrant_list_sources,
    qdrant_search_by_source,
    qdrant_search_by_doc_id,
    qdrant_search_by_source_embedding,
    extract_pages_text_pdfplumber,
)
from archive_manager.domain.automotive_parser import extract_automotive_facts, extract_labeled_values, extract_not_performed_services, extract_not_performed_service_costs, extract_performed_services, extract_service_advisor, extract_service_causes
from archive_manager.security.access_policy import authorized_event_ids, is_source_authorized, is_authorized
from archive_manager.security.security_config import validate_sensitive_configuration
from archive_manager.lifecycle.audit import record_query_audit
from archive_manager.domain.domain_parsers import get_parser
from archive_manager.core.event_facts import load_event_facts
from archive_manager.retrieval.query_planner import plan_query, route_query
from archive_manager.domain.response_validation import validate_summary
from archive_manager.retrieval.hybrid_retrieval import lexical_search
from archive_manager.lifecycle.trace_log import new_run_id, trace_event
from archive_manager.retrieval.query_handlers import QueryHandlerRegistry
from archive_manager.core.event_manifests import find_manifest_for_source, load_manifests
from archive_manager.paths import PROJECT_ROOT

REQUEST_SESSION = requests.Session()
DEFAULT_ARTIFACT_OUTPUT_DIR = PROJECT_ROOT / "artifact_output"
ARTIFACT_OUTPUT_DIR = Path(
    os.environ.get("ARTIFACT_OUTPUT_DIR", DEFAULT_ARTIFACT_OUTPUT_DIR)
)
EVENT_MANIFEST_PATH = Path(
    os.environ.get("EVENT_MANIFEST_PATH", PROJECT_ROOT / "data" / "events.json")
)
SAVE_REPORT_ARTIFACT = os.environ.get("SAVE_REPORT_ARTIFACT", "0").lower() in {"1", "true", "yes", "on"}
REPORT_ARTIFACT_WRITTEN = False


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:40] or "query"


def _save_report_artifact(question: str, hits, answer_text: str, model: str, top_k: int) -> str | None:
    """Persist a local Markdown report when report export is enabled.

    The report is intentionally scoped to local workspace storage and includes only
    the retrieved excerpts needed to support the answer. This keeps the output safe,
    compact, and suitable for downstream review or documentation.
    """
    if not SAVE_REPORT_ARTIFACT:
        return None

    global REPORT_ARTIFACT_WRITTEN

    ARTIFACT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = ARTIFACT_OUTPUT_DIR / f"report_{_safe_slug(question)}_{stamp}.md"

    lines = [
        "# Archive Query Report",
        "",
        f"- Generated at: {stamp}",
        f"- Model: {model}",
        f"- Top K: {top_k}",
        "- Scope: local workspace artifact export",
        "",
        "## Question",
        "",
        question,
        "",
        "## Retrieved evidence",
        "",
    ]

    for i, hit in enumerate(hits[:10], start=1):
        payload = hit.get("payload", {})
        doc_id = str(payload.get("doc_id", ""))
        page = payload.get("page", "?")
        chunk_index = payload.get("chunk_index", "?")
        text = str(payload.get("text", "")).strip()
        excerpt = text[:1200]
        lines.extend(
            [
                f"### Source {i}",
                "",
                f"- doc_id: {doc_id}",
                f"- page: {page}",
                f"- chunk_index: {chunk_index}",
                "",
                "```text",
                excerpt,
                "```",
                "",
            ]
        )

    lines.extend(["## Answer", "", answer_text.strip(), ""])
    report_path.write_text("\n".join(lines), encoding="utf-8")

    try:
        os.chmod(report_path, 0o600)
    except OSError:
        pass

    REPORT_ARTIFACT_WRITTEN = True

    return str(report_path)


def ollama_chat(model: str, messages, session=None):
    """Send a chat completion request to the Ollama API and return the text reply."""
    session = session or REQUEST_SESSION
    temperature = float(os.environ.get("OLLAMA_TEMPERATURE", "0"))
    seed = int(os.environ.get("OLLAMA_SEED", "42"))
    top_p = float(os.environ.get("OLLAMA_TOP_P", "0.2"))
    top_k = int(os.environ.get("OLLAMA_TOP_K", "20"))
    num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", "16384"))
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "seed": seed,
            "top_p": top_p,
            "top_k": top_k,
            "num_ctx": num_ctx,
        },
    }

    def request_chat(request_model):
        payload["model"] = request_model
        response = session.post(
            f"{OLLAMA_BASE}/api/chat",
            json=payload,
            timeout=300,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]

    try:
        return request_chat(model)
    except requests.HTTPError as exc:
        error_text = exc.response.text if exc.response is not None else ""
        fallback_model = os.environ.get("FALLBACK_ANSWER_MODEL", "gemma3:4b")
        if (
            "llama-server process has terminated" not in error_text
            and "model" not in error_text.lower()
        ) or fallback_model == model:
            raise
        print(f"[query] answer_model_failed={model}; retrying_with={fallback_model}")
        return request_chat(fallback_model)


def _summarize_hits(hits, max_excerpt_chars: int = 1200):
    """Convert vector-search hits into compact excerpts for a grounded prompt."""
    excerpts = []
    for i, h in enumerate(hits):
        payload = h.get("payload", {})
        text = str(payload.get("text", "")).strip()
        doc_id = payload.get("doc_id", "")
        page = payload.get("page", "?")
        excerpt = text[:max_excerpt_chars]
        source = payload.get("source", "")
        record_key = payload.get("record_key", "")
        excerpts.append(
            f"[{i + 1}] record={record_key}, source={source}, doc_id={doc_id}, page={page}\n{excerpt}"
        )
    return excerpts


def _filename_candidates(question: str):
    """Return document filename candidates explicitly mentioned in a question."""
    candidates = []
    for match in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(?:pdf|png|jpg|jpeg)", question, re.IGNORECASE):
        if match.lower() not in {candidate.lower() for candidate in candidates}:
            candidates.append(match)
    return candidates


def _named_source_candidates(question: str) -> list[str]:
    """Return indexed source names explicitly present in a question, including spaces."""
    indexed_sources = load_indexed_sources()
    question_folded = question.casefold()
    matches = [source for source in indexed_sources if source.casefold() in question_folded]
    return matches or _filename_candidates(question)


def _filename_regex_candidates(question: str):
    """Return regex patterns explicitly requested for filename matching."""
    patterns = re.findall(r"filename_regex\s*[:=]\s*(\S+)", question, re.IGNORECASE)
    return [pattern.strip("'\"") for pattern in patterns]


def _is_broad_query(question: str) -> bool:
    """Compatibility wrapper around the single typed query planner."""
    return plan_query(question).intent == "broad_scope"


def _clarification_for_broad_query():
    """Return a concise scope request for an archive-wide question."""
    return (
        "That question is broad across the entire archive. Please narrow it by "
        "providing a filename, filename_regex=..., topic, date range, or document type."
    )


def _record_key_for_source(source: str, source_to_doc_id: dict) -> str:
    """Return an invoice identifier extracted from one source's OCR text."""
    doc_id = source_to_doc_id.get(source)
    if not doc_id:
        return source
    try:
        text = (SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8")
    except OSError:
        return source
    match = re.search(r"\b(\d{6})\b[\s\S]{0,100}?\*INVOICE\*", text, re.IGNORECASE)
    return f"invoice:{match.group(1)}" if match else source


def _group_sources_into_records(sources):
    """Group page/image sources into invoice-level service records."""
    cache = load_ingest_cache()
    source_to_doc_id = {source: doc_id for doc_id, source in cache.items()}
    manifests = load_manifests(EVENT_MANIFEST_PATH)
    manifest_groups = {}
    ungrouped = []
    for source in sources:
        manifest = find_manifest_for_source(manifests, source)
        if manifest and is_authorized(manifest):
            manifest_groups.setdefault(manifest.event_id, []).append(source)
        elif manifest is None:
            ungrouped.append(source)

    groups = {}
    for event_id, event_sources in manifest_groups.items():
        manifest = manifests[event_id]
        ordered = [page.source_filename for page in manifest.ordered_pages()]
        groups[f"event:{event_id}"] = [source for source in ordered if source in event_sources]

    for source in ungrouped:
        key = _record_key_for_source(source, source_to_doc_id)
        groups.setdefault(key, []).append(source)
    return list(groups.items())


def _record_facts(record_sources):
    """Extract validated domain facts from all OCR pages in one event."""
    cache = load_ingest_cache()
    manifests = load_manifests(EVENT_MANIFEST_PATH)
    manifest = next(
        (candidate for candidate in manifests.values() if any(
            candidate.page_for(source) for source in record_sources
        )),
        None,
    )
    domain = manifest.event_type if manifest else "automotive_service"
    if manifest:
        cached = load_event_facts().get(manifest.event_id)
        if cached:
            return cached
    texts = []
    for source in record_sources:
        doc_id = next((key for key, value in cache.items() if value == source), None)
        if not doc_id:
            continue
        try:
            text = (SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8")
        except OSError:
            continue
        texts.append(text)
    if domain == "automotive_service":
        parsed = extract_automotive_facts(texts)
        return {
            "domain": domain,
            "vin": parsed["vin"],
            "service_date": parsed["service_date"],
            "total_charges": parsed["total_charges"],
            "fields": [],
            "warnings": parsed["warnings"],
        }

    parsed = get_parser(domain).parse(texts)
    return {
        "domain": parsed.domain,
        "vin": None,
        "service_date": None,
        "total_charges": None,
        "fields": [
            {
                "name": field.name,
                "value": field.value,
                "page": field.page,
                "confidence": field.confidence,
            }
            for field in parsed.fields
        ],
        "warnings": parsed.warnings,
    }


def _document_balanced_hits(query_embedding, record_groups, chunks_per_source: int = 3):
    """Retrieve relevant chunks from every source, fully for manifest events."""
    hits = []
    for record_key, sources in record_groups:
        for source in sources:
            if record_key.startswith("event:"):
                source_hits = qdrant_search_by_source(source, limit=100)
            else:
                source_hits = qdrant_search_by_source_embedding(
                    query_embedding, source, limit=chunks_per_source
                )
            source_hits.sort(
                key=lambda hit: (
                    hit.get("payload", {}).get("page", 0),
                    hit.get("payload", {}).get("chunk_index", 0),
                )
            )
            for hit in source_hits:
                hit.setdefault("payload", {})["record_key"] = record_key
            hits.extend(source_hits)
    return hits


def _hits_by_record(hits):
    """Group retrieved chunks by the invoice record key added during retrieval."""
    grouped = {}
    for hit in hits:
        record_key = hit.get("payload", {}).get("record_key", "unknown-record")
        grouped.setdefault(record_key, []).append(hit)
    return grouped


def _summarize_one_record(question, record_key, hits, model, max_excerpt_chars, facts):
    """Ask the LLM to extract one service record from only its own evidence."""
    context = "\n\n".join(_summarize_hits(hits, max_excerpt_chars=max_excerpt_chars))
    system = (
        "You are extracting one document event from OCR evidence. Use only the supplied "
        "evidence. Return a concise record summary with the invoice or record identifier, "
        "relevant dates, document-specific details, and amounts when present. Do not discuss any "
        "other record. Do not invent values; state when a requested value is not found. "
        "Use only a labeled TOTAL CHARGES amount for total charges. Never use a line total, "
        "estimate, labor amount, parts amount, sales tax, or recommended-but-not-performed "
        "amount as the record total. Exclude declined or recommended work from performed services. "
        "The locked record facts below are authoritative and must be copied exactly."
    )
    validated_fields = facts.get("fields", [])
    field_lines = []
    if isinstance(validated_fields, list):
        for field in validated_fields:
            if isinstance(field, dict):
                field_lines.append(
                    f"- {field.get('name')}: {field.get('value')} "
                    f"(page {field.get('page')}, confidence {field.get('confidence')})"
                )
    user = (
        f"Record identifier: {record_key}\nDomain: {facts.get('domain', 'general_document')}\n"
        f"Question: {question}\n"
        f"Locked vehicle VIN: {facts.get('vin') or 'not found'}\n"
        f"Locked facts: service_date={facts.get('service_date') or 'not found'}, "
        f"total_charges={facts.get('total_charges') or 'not found'}\n"
        f"Validated domain fields:\n{chr(10).join(field_lines) or '- none'}\n"
        f"Validation warnings: {', '.join(facts.get('warnings', [])) or 'none'}\n\n"
        f"Evidence:\n{context}\n\nReturn only the summary for this record. "
        "If the request asks for a table, use one consistent Markdown row with columns: "
        "Invoice, Service Date, Vehicle, Services Performed, Total Charges."
    )
    return ollama_chat(
        model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        session=REQUEST_SESSION,
    ).strip()


def _enforce_locked_facts(summary: str, facts: dict[str, object]) -> str:
    """Append deterministic fields so the model cannot omit validated facts."""
    locked_lines = ["", "Validated fields:"]
    if facts.get("vin"):
        locked_lines.append(f"- VIN: {facts['vin']}")
    if facts.get("service_date"):
        locked_lines.append(f"- Service date: {facts['service_date']}")
    if facts.get("total_charges"):
        locked_lines.append(f"- Total charges: ${facts['total_charges']}")
    fields = facts.get("fields", [])
    if isinstance(fields, list):
        for field in fields:
            if isinstance(field, dict) and field.get("name") and field.get("value"):
                locked_lines.append(
                    f"- {field['name']}: {field['value']} "
                    f"(page {field.get('page')}, confidence {field.get('confidence')})"
                )
    warnings = facts.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = []
    for warning in warnings:
        locked_lines.append(f"- Warning: {warning}")
    response_check = validate_summary(summary, facts)
    if response_check["status"] == "invalid":
        for warning in response_check["warnings"]:
            locked_lines.append(f"- Model validation: {warning}")
    return summary.rstrip() + "\n" + "\n".join(locked_lines) if len(locked_lines) > 1 else summary


def _is_source_inventory_request(question: str) -> bool:
    """Compatibility wrapper around the single typed query planner."""
    return plan_query(question).intent == "source_inventory"


def _requested_service_date(question: str) -> str | None:
    """Convert common natural-language dates in a question to ISO format."""
    months = {name: index for index, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
        start=1,
    )}
    match = re.search(r"\b([a-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})\b", question, re.IGNORECASE)
    if match:
        month = months.get(match.group(1)[:3].upper())
        if month:
            return date(int(match.group(3)), month, int(match.group(2))).isoformat()
    match = re.search(r"\b(\d{1,2})\s+([a-z]{3,9})\s+(20\d{2})\b", question, re.IGNORECASE)
    if match:
        month = match.group(2)[:3].upper()
        if month in months:
            return date(int(match.group(3)), months[month], int(match.group(1))).isoformat()
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", question)
    if match:
        month_names = (
            "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
            "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
        )
        first = int(match.group(1))
        second = int(match.group(2))
        month, day = (first, second) if first <= 12 else (second, first)
        if 1 <= month <= 12 and 1 <= day <= 31:
            return date(int(match.group(3)), month, day).isoformat()
    match = re.search(r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b", question)
    if match:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    match = re.search(r"\b(\d{2}[A-Z]{3}\d{2})\b", question, re.IGNORECASE)
    return _format_service_date(match.group(1).upper()) if match else None


def _performed_services_for_date(question: str) -> list[str]:
    """Collect performed services from all OCR documents matching a requested date."""
    target_date = _requested_service_date(question)
    if not target_date:
        return []
    services = []
    cache = load_ingest_cache()
    for doc_id, source in cache.items():
        try:
            text = (SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = extract_automotive_facts([text])
        candidate_dates = {_format_service_date(candidate) for candidate in parsed.get("service_date_candidates", [])}
        if target_date not in candidate_dates:
            continue
        for service in extract_performed_services([text]):
            if service not in services:
                services.append(service)
    return services


def _performed_services_for_all_records() -> list[tuple[str, list[str], str | None]]:
    """Collect performed services grouped by authorized service event."""
    sources = _authorized_sources(load_indexed_sources())
    records = _group_sources_into_records(sources)
    cache = load_ingest_cache()
    cached_facts = load_event_facts()
    results = []
    for record_key, record_sources in records:
        manifest_event_id = record_key.removeprefix("event:") if record_key.startswith("event:") else None
        facts = cached_facts.get(manifest_event_id, {}) if manifest_event_id else {}
        services = list(facts.get("services_performed", [])) if isinstance(facts, dict) else []
        service_date = facts.get("service_date") if isinstance(facts, dict) else None
        total = facts.get("total_charges") if isinstance(facts, dict) else None

        texts = []
        if not services or not service_date or not total:
            for source in record_sources:
                doc_id = next((key for key, value in cache.items() if value == source), None)
                if not doc_id:
                    continue
                try:
                    texts.append((SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8"))
                except OSError:
                    continue
            parsed = extract_automotive_facts(texts)
            service_date = service_date or parsed.get("service_date")
            services = services or extract_performed_services(texts)
            total = total or parsed.get("total_charges")

        if service_date and services:
            results.append((_format_service_date(str(service_date)), list(dict.fromkeys(services)), str(total) if total else None))
    return sorted(results, key=lambda item: item[0])


def _not_performed_services_for_all_records() -> list[tuple[str, list[str]]]:
    """Collect excluded/recommended services grouped by authorized event date."""
    sources = _authorized_sources(load_indexed_sources())
    records = _group_sources_into_records(sources)
    cache = load_ingest_cache()
    results = []
    for record_key, record_sources in records:
        texts = []
        for source in record_sources:
            doc_id = next((key for key, value in cache.items() if value == source), None)
            if doc_id:
                try:
                    texts.append((SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8"))
                except OSError:
                    pass
        parsed = extract_automotive_facts(texts)
        services = extract_not_performed_services(texts)
        if parsed.get("service_date") and services:
            results.append((_format_service_date(str(parsed["service_date"])), services))
    return sorted(results, key=lambda item: item[0])


def _not_performed_services_with_costs() -> list[tuple[str, list[tuple[str, str | None]]]]:
    """Collect excluded services and explicit costs grouped by date."""
    sources = _authorized_sources(load_indexed_sources())
    records = _group_sources_into_records(sources)
    cache = load_ingest_cache()
    results = []
    for record_key, record_sources in records:
        texts = []
        for source in record_sources:
            doc_id = next((key for key, value in cache.items() if value == source), None)
            if doc_id:
                try:
                    texts.append((SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8"))
                except OSError:
                    pass
        parsed = extract_automotive_facts(texts)
        services = extract_not_performed_service_costs(texts)
        if parsed.get("service_date") and services:
            results.append((_format_service_date(str(parsed["service_date"])), services))
    return sorted(results, key=lambda item: item[0])


def _service_advisors_for_all_records() -> list[tuple[str, str]]:
    """Collect one labeled service advisor per authorized service event."""
    sources = _authorized_sources(load_indexed_sources())
    records = _group_sources_into_records(sources)
    cache = load_ingest_cache()
    results = []
    for record_key, record_sources in records:
        texts = []
        for source in record_sources:
            doc_id = next((key for key, value in cache.items() if value == source), None)
            if doc_id:
                try:
                    texts.append((SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8"))
                except OSError:
                    pass
        parsed = extract_automotive_facts(texts)
        advisor = extract_service_advisor(texts)
        if parsed.get("service_date") and advisor:
            results.append((_format_service_date(str(parsed["service_date"])), advisor))
    return sorted(set(results))


def _repair_causes_for_all_records(requested_date: str | None = None) -> list[tuple[str, list[tuple[str, str]]]]:
    """Collect labeled repair causes grouped by service date."""
    sources = _authorized_sources(load_indexed_sources())
    records = _group_sources_into_records(sources)
    cache = load_ingest_cache()
    results = []
    for record_key, record_sources in records:
        texts = []
        for source in record_sources:
            doc_id = next((key for key, value in cache.items() if value == source), None)
            if doc_id:
                try:
                    texts.append((SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8"))
                except OSError:
                    pass
        parsed = extract_automotive_facts(texts)
        causes = extract_service_causes(texts)
        candidate_dates = {_format_service_date(candidate) for candidate in parsed.get("service_date_candidates", [])}
        if requested_date and requested_date not in candidate_dates:
            continue
        causes = [
            (repair, cause)
            for repair, cause in causes
            if not re.search(r"lube oil|oil and filter|completed lube", f"{repair} {cause}", re.IGNORECASE)
        ]
        if parsed.get("service_date") and causes:
            results.append((_format_service_date(str(parsed["service_date"])), causes))
    return sorted(results, key=lambda item: item[0])


def _format_repair_causes(records: list[tuple[str, list[tuple[str, str]]]]) -> str:
    if not records:
        return "No labeled repair causes were found in the archive."
    return "Repair causes by date:\n" + "\n\n".join(
        f"{date_value}:\n" + "\n".join(f"- Repair: {repair}\n  Cause: {cause}" for repair, cause in causes)
        for date_value, causes in records
    )


def _format_service_advisors(records: list[tuple[str, str]]) -> str:
    if not records:
        return "No service advisors were found in the archive."
    return "Service advisors by date:\n" + "\n".join(f"- {date_value}: {advisor}" for date_value, advisor in records)


def _label_values_for_all_records(labels: tuple[str, ...], requested_date: str | None = None) -> list[tuple[str, dict[str, list[str]]]]:
    sources = _authorized_sources(load_indexed_sources())
    records = _group_sources_into_records(sources)
    cache = load_ingest_cache()
    results = []
    for record_key, record_sources in records:
        texts = []
        for source in record_sources:
            doc_id = next((key for key, value in cache.items() if value == source), None)
            if doc_id:
                try:
                    texts.append((SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8"))
                except OSError:
                    pass
        parsed = extract_automotive_facts(texts)
        service_date = _format_service_date(str(parsed["service_date"])) if parsed.get("service_date") else None
        candidate_dates = {
            _format_service_date(str(candidate))
            for candidate in parsed.get("service_date_candidates", [])
        }
        if requested_date and requested_date not in candidate_dates:
            continue
        display_date = requested_date or service_date
        values = extract_labeled_values(texts, list(labels))
        for label in labels:
            key = label.upper()
            values.setdefault(key, [])
        if display_date:
            results.append((display_date, values))
    return sorted(results, key=lambda item: item[0])


def _source_texts(source: str, cache: dict[str, str]) -> list[str]:
    """Load one source's text from its sidecar or searchable PDF."""
    doc_id = next((key for key, value in cache.items() if value == source), None)
    if not doc_id:
        return []
    text_path = SEARCHABLE_DIR / f"{doc_id}.txt"
    if text_path.exists():
        return [text_path.read_text(encoding="utf-8")]
    pdf_path = SEARCHABLE_DIR / f"{doc_id}.pdf"
    if not pdf_path.exists():
        return []
    return [page["text"] for page in extract_pages_text_pdfplumber(pdf_path)]


def _extract_inline_label_values(texts: list[str]) -> dict[str, list[str]]:
    """Extract repeated inline ``label: value`` pairs from general documents."""
    values: dict[str, list[str]] = {}
    current_question: tuple[str, list[str]] | None = None
    question_complete = False
    quiz_document = False
    question_pattern = re.compile(r"\bQuestion\s+(\d+)\s+(.+)$", re.IGNORECASE)
    metadata_patterns = (
        ("Started on", re.compile(r"\bStarted on\s+(.+)$", re.IGNORECASE)),
        ("State", re.compile(r"\bState\s+([A-Za-z]+)\b", re.IGNORECASE)),
        ("Completed on", re.compile(r"\bCompleted on\s+(.+)$", re.IGNORECASE)),
        ("Time taken", re.compile(r"\bTime taken\s+(.+)$", re.IGNORECASE)),
        ("Points", re.compile(r"\bPoints\s+([0-9.]+\s*/\s*[0-9.]+)", re.IGNORECASE)),
        ("Grade", re.compile(r"\bGrade\s+(.+)$", re.IGNORECASE)),
    )
    for text in texts:
        for raw_line in text.splitlines():
            line = " ".join(raw_line.split()).strip()
            metadata_match = next(
                ((label, pattern.search(line)) for label, pattern in metadata_patterns if pattern.search(line)),
                None,
            )
            if metadata_match:
                label, match = metadata_match
                value = " ".join(match.group(1).split()).strip()
                values.setdefault(label, [])
                if value not in values[label]:
                    values[label].append(value)
            question_match = question_pattern.search(line)
            if question_match:
                quiz_document = True
                question_text = re.split(r"\s+(?:Correct|Incorrect)\b", question_match.group(2), maxsplit=1, flags=re.IGNORECASE)[0]
                question_text = re.split(r"\s+of\s+1\.00\b|\s+Select one\b|\s+Flag question\b", question_text, maxsplit=1, flags=re.IGNORECASE)[0]
                current_question = (question_match.group(1), [question_text.strip()])
                question_complete = bool(question_text != question_match.group(2))
                continue
            if current_question and re.fullmatch(r"(?:correct|incorrect)", line, re.IGNORECASE):
                question_complete = True
                continue
            if current_question and re.match(r"^(?:The )?correct answer is\s*:", line, re.IGNORECASE):
                answer = re.sub(r"^(?:The )?correct answer is\s*:\s*", "", line, flags=re.IGNORECASE).strip()
                question_number, question_parts = current_question
                question_text = " ".join(question_parts)
                question_text = re.split(r"\s+(?:Correct|Incorrect)\b|\s+of\s+1\.00\b|\s+Select one\b|\s+Flag question\b", question_text, maxsplit=1, flags=re.IGNORECASE)[0].strip()
                label = f"Question {question_number}: {question_text}"
                values.setdefault(label, [])
                if answer and answer not in values[label]:
                    values[label].append(answer)
                current_question = None
                question_complete = False
                continue
            if current_question and not question_complete:
                if re.search(r"\b(?:Correct|Incorrect)\b", line, re.IGNORECASE):
                    question_complete = True
                    continue
                if re.match(r"^(?:The following|Although|Certain|Patients|This|These)\b", line, re.IGNORECASE):
                    continue
                if line.startswith(("a.", "b.", "c.", "d.")) or re.match(r"^\d+\.\d+ points", line, re.IGNORECASE):
                    continue
                current_question[1].append(line)
                continue
            if metadata_match:
                continue
            if quiz_document:
                continue
            if ":" not in line:
                continue
            label, value = (part.strip(" -*") for part in line.split(":", 1))
            if (
                not label
                or not value
                or len(label) > 50
                or "/" in label
                or label.count(" ") > 6
                or label.endswith((".", ",", ";"))
            ):
                continue
            values.setdefault(label, [])
            if value not in values[label]:
                values[label].append(value)
    return values


def _label_values_for_named_sources(source_names: list[str]) -> list[tuple[str, dict[str, list[str]]]]:
    """Extract generic label/value pairs from explicitly named documents."""
    cache = load_ingest_cache()
    results = []
    for source in source_names:
        values = _extract_inline_label_values(_source_texts(source, cache))
        if values:
            results.append((source, values))
    return results


def _format_named_label_values(records: list[tuple[str, dict[str, list[str]]]], question: str = "") -> str:
    if not records:
        return "No labeled values were found in the requested document."
    omit_questions = bool(re.search(r"\b(?:do not|don't|without|excluding)\b[^.\n]*\bquestions?\b", question, re.IGNORECASE))
    lines = []
    for source, values in records:
        lines.append(f"Label values for {source}:")
        for label, entries in values.items():
            if omit_questions and re.match(r"Question\s+\d+\s*:", label, re.IGNORECASE):
                continue
            normalized_entries = []
            seen_entries = set()
            for entry in entries:
                normalized = re.sub(r":\s+", ":", " ".join(entry.split())).strip()
                comparison_key = normalized.casefold()
                if comparison_key not in seen_entries:
                    seen_entries.add(comparison_key)
                    normalized_entries.append(normalized)
            lines.append(f"- {label}: {'; '.join(normalized_entries)}")
    return "\n".join(lines)


def _format_label_values(records, labels: tuple[str, ...], requested_date: str | None = None) -> str:
    if not records:
        return f"No labeled values were found for {requested_date or 'the requested date'}."
    lines = ["Label values by service date:"] if not requested_date else [f"Label values for {requested_date}:"]
    for service_date, values in records:
        lines.append(f"{service_date}:")
        for label in labels:
            entries = values.get(label.upper(), [])
            lines.append(f"- {label}: {'; '.join(entries) if entries else 'not found'}")
    return "\n".join(lines) if len(lines) > 1 else "No labeled values were found in the archive."


def _format_not_performed_services(records: list[tuple[str, list[str]]]) -> str:
    if not records:
        return "No not-performed or declined services were found in the archive."
    return "Car services not performed by date:\n" + "\n\n".join(
        f"{date_value}:\n" + "\n".join(f"- {service}" for service in services)
        for date_value, services in records
    )


def _format_not_performed_services_with_costs(records) -> str:
    if not records:
        return "No not-performed or declined services were found in the archive."
    rows = []
    for date_value, services in records:
        for service, cost in services:
            rows.append((cost is None, cost or "", date_value, service))
    rows.sort(key=lambda row: (row[0], float(row[1].replace(",", "")) if row[1] else 0, row[2], row[3]))
    has_known_cost = any(cost for _, cost, _, _ in rows)
    heading = (
        "Car services not performed ordered by cost (lowest to highest):"
        if has_known_cost
        else "Car services not performed (individual costs not stated; cannot reliably order by cost):"
    )
    return heading + "\n" + "\n".join(
        f"- {date_value}: {service} (cost not stated)" if cost == "" else f"- {date_value}: {service} (${cost})"
        for _, cost, date_value, service in rows
    )


def _format_performed_services_inventory(records, include_total_cost: bool = False) -> str:
    """Format performed services as one section per service date."""
    if not records:
        return "No performed car services were found in the archive."
    sections = []
    for record in records:
        service_date, services = record[:2]
        total = record[2] if len(record) > 2 else None
        lines = [f"{service_date}:", *[f"- {service}" for service in services]]
        if include_total_cost and total:
            lines.append(f"- Total charges: ${total}")
        sections.append("\n".join(lines))
    return "Performed car services by date:\n" + "\n\n".join(sections)


def _sort_service_records_by_cost(records):
    """Sort records by known numeric total, with missing totals last."""
    return sorted(
        records,
        key=lambda record: (
            record[2] is None,
            float(record[2].replace(",", "")) if record[2] else 0,
            record[0],
        ),
    )


def _format_performed_services_markdown_table(records, include_total_cost: bool = False, cost_first: bool = False, include_grand_total: bool = False) -> str:
    """Render grouped performed services as a GitHub-Flavored Markdown table."""
    if not records:
        return "No performed car services were found in the archive."
    headers = ["Service Date", "Services Performed"]
    if include_total_cost:
        headers.append("Total Charges")
    rows = []
    for record in records:
        service_date, services = record[:2]
        total = record[2] if len(record) > 2 else None
        row = [service_date, "; ".join(services)]
        if include_total_cost:
            row.append(f"${total}" if total else "Not found")
        rows.append(row)
    if cost_first and include_total_cost:
        headers = ["Total Charges", "Service Date", "Services Performed"]
        rows = [[row[2], row[0], row[1]] for row in rows]
    if include_total_cost and include_grand_total:
        combined_total = _sum_total_charges([(record[0], record[2]) for record in records if len(record) > 2])
        rows.append(["Combined Total", "", f"${combined_total}"] if not cost_first else [f"${combined_total}", "Combined Total", ""])
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _format_performed_services_ascii_table(records, include_total_cost: bool = False, cost_first: bool = False) -> str:
    """Render grouped performed services as copyable plain ASCII."""
    headers = ["Service Date", "Services Performed"]
    if include_total_cost:
        headers.append("Total Charges")
    rows = []
    for record in records:
        service_date, services = record[:2]
        total = record[2] if len(record) > 2 else None
        rows.append([service_date, "; ".join(services), f"${total}" if total else "Not found"] if include_total_cost else [service_date, "; ".join(services)])
    if cost_first and include_total_cost:
        headers = ["Total Charges", "Service Date", "Services Performed"]
        rows = [[row[2], row[0], row[1]] for row in rows]
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(len(headers))]
    separator = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    lines = [separator, "|" + "|".join(f" {headers[index]:<{widths[index]}} " for index in range(len(headers))) + "|", separator]
    lines.extend("|" + "|".join(f" {row[index]:<{widths[index]}} " for index in range(len(headers))) + "|" for row in rows)
    lines.append(separator)
    return "\n".join(lines)


def _mermaid_sanitize(text: str) -> str:
    """Escape characters that would break a Mermaid node label."""
    return text.replace('"', "'").replace("\n", " ").strip()


def _format_grouped_markdown_table(sections: list[tuple[str, list[str]]], group_header: str, items_header: str) -> str:
    """Render (group, items) sections as a GitHub-Flavored Markdown table."""
    if not sections:
        return "No data was found in the archive."
    lines = [f"| {group_header} | {items_header} |", "| --- | --- |"]
    lines.extend(f"| {group} | {'; '.join(items)} |" for group, items in sections)
    return "\n".join(lines)


def _format_grouped_ascii_table(sections: list[tuple[str, list[str]]], group_header: str, items_header: str) -> str:
    """Render (group, items) sections as a plain fixed-width ASCII table."""
    if not sections:
        return "No data was found in the archive."
    headers = [group_header, items_header]
    rows = [[group, "; ".join(items)] for group, items in sections]
    widths = [max(len(headers[index]), *(len(row[index]) for row in rows)) for index in range(len(headers))]
    separator = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    lines = [separator, "|" + "|".join(f" {headers[index]:<{widths[index]}} " for index in range(len(headers))) + "|", separator]
    lines.extend("|" + "|".join(f" {row[index]:<{widths[index]}} " for index in range(len(headers))) + "|" for row in rows)
    lines.append(separator)
    return "\n".join(lines)


def _format_grouped_flowchart(sections: list[tuple[str, list[str]]]) -> str:
    """Render (group, items) sections as a Mermaid process flowchart, one branch per group."""
    if not sections:
        return "No data was found in the archive."
    lines = ["```mermaid", "flowchart TD"]
    for group, items in sections:
        group_id = f"g{re.sub(r'[^0-9A-Za-z]', '', group)}"
        previous_id = group_id
        lines.append(f'    {group_id}(["{_mermaid_sanitize(group)}"])')
        for index, item in enumerate(items):
            step_id = f"{group_id}_s{index}"
            lines.append(f'    {previous_id} --> {step_id}["{_mermaid_sanitize(item)}"]')
            previous_id = step_id
    lines.append("```")
    return "\n".join(lines)


def _format_grouped_sequence_diagram(sections: list[tuple[str, list[str]]], actor_a: str = "Vehicle", actor_b: str = "Shop") -> str:
    """Render (group, items) sections as a Mermaid sequence diagram between two actors."""
    if not sections:
        return "No data was found in the archive."
    lines = ["```mermaid", "sequenceDiagram", f"    participant {actor_a}", f"    participant {actor_b}"]
    for group, items in sections:
        lines.append(f"    Note over {actor_a},{actor_b}: {_mermaid_sanitize(group)}")
        for item in items:
            lines.append(f"    {actor_a}->>{actor_b}: {_mermaid_sanitize(item)}")
    lines.append("```")
    return "\n".join(lines)


def _format_grouped_component_diagram(sections: list[tuple[str, list[str]]]) -> str:
    """Render (group, items) sections as a Mermaid diagram with one subgraph per group."""
    if not sections:
        return "No data was found in the archive."
    lines = ["```mermaid", "flowchart LR"]
    for group, items in sections:
        group_id = f"g{re.sub(r'[^0-9A-Za-z]', '', group)}"
        lines.append(f'    subgraph {group_id} ["{_mermaid_sanitize(group)}"]')
        for index, item in enumerate(items):
            lines.append(f'        {group_id}_c{index}["{_mermaid_sanitize(item)}"]')
        lines.append("    end")
    lines.append("```")
    return "\n".join(lines)


def _dispatch_grouped_format(output_format: str, sections: list[tuple[str, list[str]]], group_header: str, items_header: str, default_text: str) -> str:
    """Render sections in the requested table/diagram format, falling back to plain text."""
    formatters = {
        "markdown_table": lambda: _format_grouped_markdown_table(sections, group_header, items_header),
        "ascii_table": lambda: _format_grouped_ascii_table(sections, group_header, items_header),
        "flowchart": lambda: _format_grouped_flowchart(sections),
        "sequence_diagram": lambda: _format_grouped_sequence_diagram(sections),
        "component_diagram": lambda: _format_grouped_component_diagram(sections),
    }
    return formatters.get(output_format, lambda: default_text)()


def _format_performed_services_flowchart(records, include_total_cost: bool = False) -> str:
    """Render grouped performed services as a Mermaid process flowchart, one branch per date."""
    if not records:
        return "No performed car services were found in the archive."
    lines = ["```mermaid", "flowchart TD"]
    for record in records:
        service_date, services = record[:2]
        total = record[2] if len(record) > 2 else None
        date_id = f"d{re.sub(r'[^0-9A-Za-z]', '', service_date)}"
        previous_id = date_id
        lines.append(f'    {date_id}(["{_mermaid_sanitize(service_date)}"])')
        for index, service in enumerate(services):
            step_id = f"{date_id}_s{index}"
            lines.append(f'    {previous_id} --> {step_id}["{_mermaid_sanitize(service)}"]')
            previous_id = step_id
        if include_total_cost and total:
            total_id = f"{date_id}_total"
            lines.append(f'    {previous_id} --> {total_id}(["Total: ${_mermaid_sanitize(total)}"])')
    lines.append("```")
    return "\n".join(lines)


def _format_performed_services_sequence_diagram(records, include_total_cost: bool = False) -> str:
    """Render grouped performed services as a Mermaid sequence diagram between Vehicle and Shop."""
    if not records:
        return "No performed car services were found in the archive."
    lines = ["```mermaid", "sequenceDiagram", "    participant Vehicle", "    participant Shop"]
    for record in records:
        service_date, services = record[:2]
        total = record[2] if len(record) > 2 else None
        lines.append(f"    Note over Vehicle,Shop: {_mermaid_sanitize(service_date)}")
        for service in services:
            lines.append(f"    Vehicle->>Shop: {_mermaid_sanitize(service)}")
        if include_total_cost and total:
            lines.append(f"    Shop-->>Vehicle: Total charges ${_mermaid_sanitize(total)}")
    lines.append("```")
    return "\n".join(lines)


def _format_performed_services_component_diagram(records, include_total_cost: bool = False) -> str:
    """Render grouped performed services as a Mermaid diagram with one subgraph per date."""
    if not records:
        return "No performed car services were found in the archive."
    lines = ["```mermaid", "flowchart LR"]
    for record in records:
        service_date, services = record[:2]
        total = record[2] if len(record) > 2 else None
        date_id = f"d{re.sub(r'[^0-9A-Za-z]', '', service_date)}"
        lines.append(f'    subgraph {date_id} ["{_mermaid_sanitize(service_date)}"]')
        for index, service in enumerate(services):
            lines.append(f'        {date_id}_c{index}["{_mermaid_sanitize(service)}"]')
        if include_total_cost and total:
            lines.append(f'        {date_id}_total(["Total: ${_mermaid_sanitize(total)}"])')
        lines.append("    end")
    lines.append("```")
    return "\n".join(lines)



def _total_charges_for_date(question: str) -> list[str]:
    """Collect distinct labeled TOTAL CHARGES values for a requested date."""
    target_date = _requested_service_date(question)
    if not target_date:
        return []
    totals = []
    cache = load_ingest_cache()
    for doc_id, source in cache.items():
        try:
            text = (SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = extract_automotive_facts([text])
        candidate_dates = {
            _format_service_date(candidate)
            for candidate in parsed.get("service_date_candidates", [])
        }
        if target_date not in candidate_dates or not parsed.get("total_charges"):
            continue
        total = str(parsed["total_charges"])
        if total not in totals:
            totals.append(total)
    return totals


def _total_charges_for_all_records() -> list[tuple[str, str]]:
    """Collect one authoritative total for each visible service record."""
    sources = _authorized_sources(load_indexed_sources())
    records = _group_sources_into_records(sources)
    results = []
    for record_key, record_sources in records:
        texts = []
        cache = load_ingest_cache()
        for source in record_sources:
            doc_id = next((key for key, value in cache.items() if value == source), None)
            if not doc_id:
                continue
            try:
                texts.append((SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8"))
            except OSError:
                continue
        parsed = extract_automotive_facts(texts)
        total = parsed.get("total_charges")
        if not total:
            continue
        service_date = parsed.get("service_date") or record_key
        normalized_date = _format_service_date(str(service_date))
        results.append((normalized_date, str(total)))
    return sorted(set(results))


def _sum_total_charges(totals: list[tuple[str, str]]) -> str | None:
    """Sum known numeric per-date totals into one combined figure, ignoring unparsable values."""
    running_total = Decimal("0")
    found = False
    for _service_date, total in totals:
        try:
            running_total += Decimal(str(total).replace(",", ""))
            found = True
        except InvalidOperation:
            continue
    return f"{running_total:.2f}" if found else None


def _format_total_charges_inventory(totals: list[tuple[str, str]], include_grand_total: bool = False) -> str:
    """Format one total-charge row per service record, optionally with a combined sum."""
    if not totals:
        return "No total charges were found for the car service records."
    lines = [f"- {service_date}: ${total}" for service_date, total in totals]
    if include_grand_total:
        grand_total = _sum_total_charges(totals)
        if grand_total:
            lines.append(f"- Combined total: ${grand_total}")
    return "Car service total charges:\n" + "\n".join(lines)


def _format_total_charges(totals: list[str], date: str | None) -> str:
    """Format deterministic invoice total charges."""
    if not totals:
        return f"No total charges were found for {date or 'the requested date'}."
    header = f"Total charges for {date}:" if date else "Total charges:"
    return header + "\n" + "\n".join(f"- ${total}" for total in totals)


def _format_performed_services(services: list[str], date: str | None) -> str:
    """Format a deterministic performed-service response."""
    if not services:
        return f"No performed car services were found for {date or 'the requested date'}."
    header = f"Performed car services for {date}:" if date else "Performed car services:"
    return header + "\n" + "\n".join(f"- {service}" for service in services)


def _format_service_date(value: str) -> str:
    """Normalize compact invoice dates to unambiguous ISO format."""
    match = re.fullmatch(r"(\d{2})([A-Z]{3})(\d{2})", value.upper())
    if match:
        months = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }
        month = months.get(match.group(2))
        if month:
            return date(2000 + int(match.group(3)), month, int(match.group(1))).isoformat()
    return value


def _service_dates_from_archive() -> list[str]:
    """Collect one service date per visible source group without using the LLM."""
    sources = _authorized_sources(load_indexed_sources())
    cache = load_ingest_cache()
    source_to_doc_id = {source: doc_id for doc_id, source in cache.items()}
    dates = set()
    for source in sources:
        doc_id = source_to_doc_id.get(source)
        candidates = []
        if doc_id:
            try:
                text = (SEARCHABLE_DIR / f"{doc_id}.txt").read_text(encoding="utf-8")
            except OSError:
                text = ""
            if text:
                parsed = extract_automotive_facts([text])
                candidates = parsed.get("service_date_candidates", [])
        if candidates:
            dates.add(_format_service_date(candidates[0]))
            continue

        filename_date = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", source)
        if filename_date:
            year, month, day = filename_date.groups()
            month_names = (
                "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
            )
            dates.add(f"{year}-{month}-{day}")
    return sorted(dates, key=lambda value: (value[-2:], value[2:5], value[:2]))


def _format_service_date_inventory(dates: list[str]) -> str:
    """Format deterministic service dates as a concise archive inventory."""
    if not dates:
        return "No car service dates were found in the archive."
    return "Car service dates:\n" + "\n".join(f"- {date}" for date in dates)


def _format_source_inventory(sources):
    """Format an indexed filename inventory as a deterministic plain-text list."""
    if not sources:
        return "No processed document filenames were found in the archive index."
    return "Processed files:\n" + "\n".join(f"- {source}" for source in sources)


def _quiz_date_from_texts(texts: list[str]) -> str:
    """Return the first 2025 calendar date found in quiz text."""
    date_pattern = re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+\d{1,2},?\s+2025\b",
        re.IGNORECASE,
    )
    for text in texts:
        match = date_pattern.search(text)
        if match:
            return datetime.strptime(match.group(0).replace(",", ""), "%B %d %Y").date().isoformat()
    return "unknown"


def _filter_source_inventory_family(sources, question: str):
    """Narrow filename inventory requests to an explicitly named document family."""
    normalized = re.sub(r"[^a-z0-9]+", " ", question.lower())
    if re.search(r"\b(pharmtech|quiz|quizzes|quizes)\b", normalized):
        return [source for source in sources if re.search(r"(?:^|[^a-z0-9])(pharmtech|quiz)(?:[^a-z0-9]|$)", source.lower())]
    return sources


def _document_dates_from_sources(question: str) -> list[str]:
    """Extract ISO dates from filenames matching the named document family."""
    normalized = re.sub(r"[^a-z0-9]+", " ", question.lower())
    ignored = {"list", "show", "give", "what", "are", "the", "dates", "date", "for", "of", "in"}
    keywords = {word for word in normalized.split() if len(word) > 2 and word not in ignored}
    keywords = {"quiz" if word in {"quizes", "quizzes"} else word for word in keywords}
    sources = _authorized_sources(load_indexed_sources())
    if not sources:
        sources = _authorized_sources(qdrant_list_sources())
    dates = set()
    for source in sources:
        source_lower = source.lower()
        if keywords and not all(keyword in source_lower for keyword in keywords):
            continue
        match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", source)
        if match:
            dates.add("-".join(match.groups()))
    return sorted(dates)


def _quiz_questions_from_sources(question: str, limit: int = 5) -> list[tuple[str, list[str]]]:
    """Retrieve quiz sources, then return ordered questions from each source."""
    count_match = re.search(r"\b(?:first|initial|last)\s+(\d+)\s+questions?\b", question, re.IGNORECASE)
    limit = int(count_match.group(1)) if count_match else limit
    last_questions = bool(re.search(r"\blast\s+\d+\s+questions?\b", question, re.IGNORECASE))
    cache = load_ingest_cache()
    query_embedding = ollama_embed_text(question)
    dense_hits = qdrant_search(query_embedding, top_k=max(50, limit * 10)).get("result", [])
    lexical_hits = lexical_search(question, cache, SEARCHABLE_DIR, limit=max(50, limit * 10))
    retrieved_sources = {
        str(hit.get("payload", {}).get("source", "")).strip()
        for hit in [*dense_hits, *lexical_hits]
        if hit.get("payload", {}).get("source")
    }
    matching_sources = [
        source for source in _authorized_sources(retrieved_sources)
        if "quiz" in source.casefold() or "pharmtech" in source.casefold()
    ]
    matching_sources.sort(key=str.casefold)
    records = []
    for source in matching_sources:
        values = _extract_inline_label_values(_source_texts(source, cache))
        questions = [
            (label, entries)
            for label, entries in values.items()
            if re.match(r"Question\s+\d+\s*:", label, re.IGNORECASE)
        ]
        questions.sort(key=lambda item: int(re.search(r"\d+", item[0]).group(0)))
        if questions:
            records.append((source, questions[-limit:] if last_questions else questions[:limit]))
    return records


def _format_quiz_questions(records: list[tuple[str, list[tuple[str, list[str]]]]]) -> str:
    if not records:
        return "No quiz questions were found in the archive."
    lines = []
    for source, questions in records:
        lines.append(f"Questions from {source}:")
        lines.extend(f"- {label}" for label, _entries in questions)
    return "\n".join(lines)


def _quiz_topic_from_question(question: str) -> str | None:
    match = re.search(r"\bquestion\s+on\s+(.+?)\s*\??$", question, re.IGNORECASE)
    return match.group(1).strip(" .?!") if match else None


def _quiz_sources_for_topic(question: str) -> list[tuple[str, list[str]]]:
    """Use lexical and dense retrieval to find quiz sources containing a topic."""
    topic = _quiz_topic_from_question(question)
    if not topic:
        return []
    cache = load_ingest_cache()
    query_embedding = ollama_embed_text(topic)
    dense_hits = qdrant_search(query_embedding, top_k=50).get("result", [])
    lexical_hits = lexical_search(topic, cache, SEARCHABLE_DIR, limit=50)
    sources = sorted({
        str(hit.get("payload", {}).get("source", "")).strip()
        for hit in [*dense_hits, *lexical_hits]
        if hit.get("payload", {}).get("source")
        and "quiz" in str(hit["payload"].get("source", "")).casefold()
    }, key=str.casefold)
    records = []
    for source in _authorized_sources(sources):
        values = _extract_inline_label_values(_source_texts(source, cache))
        questions = [label for label in values if re.search(re.escape(topic), label, re.IGNORECASE)]
        if questions:
            records.append((source, questions))
    return records


def _format_quiz_topic_sources(records: list[tuple[str, list[str]]]) -> str:
    if not records:
        return "No quiz containing that question topic was found in the archive."
    lines = []
    for source, questions in records:
        lines.append(f"{source}:")
        lines.extend(f"- {question}" for question in questions)
    return "\n".join(lines)


def _source_for_doc_id(question: str) -> list[str]:
    match = re.search(r"\bdoc[_ ]?id\s*[=:]\s*([a-f0-9]{16,})\b", question, re.IGNORECASE)
    if not match:
        return []
    doc_id = match.group(1).lower()
    cache = load_ingest_cache()
    sources = [source for cached_id, source in cache.items() if cached_id.lower() == doc_id]
    if not sources:
        sources = [
            str(point.get("payload", {}).get("source", ""))
            for point in qdrant_search_by_doc_id(doc_id)
            if point.get("payload", {}).get("source")
        ]
    return list(dict.fromkeys(sources))


def _deterministic_handlers() -> QueryHandlerRegistry:
    """Build the registered deterministic handler set."""
    registry = QueryHandlerRegistry()

    def source_inventory(question, plan, run_id):
        sources = _authorized_sources(load_indexed_sources())
        if not sources:
            sources = _authorized_sources(qdrant_list_sources())
        sources = _filter_source_inventory_family(sources, question)
        sources = _filter_sources_by_date(sources, question)
        answer_text = _format_source_inventory(sources)
        record_query_audit(question, outcome="source_inventory")
        trace_event(run_id, "source_inventory", "END", status="completed", answer=answer_text, sources=sources)
        return answer_text

    def service_date_inventory(question, plan, run_id):
        dates = _service_dates_from_archive()
        answer_text = _format_service_date_inventory(dates)
        record_query_audit(question, hit_count=len(dates), outcome="service_date_inventory")
        trace_event(run_id, "service_date_inventory", "END", status="completed", answer=answer_text, dates=dates)
        return answer_text

    def document_date_inventory(question, plan, run_id):
        dates = _document_dates_from_sources(question)
        answer_text = (
            "Document dates:\n" + "\n".join(f"- {date}" for date in dates)
            if dates else "No matching document dates were found in the archive."
        )
        record_query_audit(question, hit_count=len(dates), outcome="document_date_inventory")
        trace_event(run_id, "document_date_inventory", "END", status="completed", answer=answer_text, dates=dates)
        return answer_text

    def quiz_question_inventory(question, plan, run_id):
        if plan and plan.requested_fields == ("question_count",):
            cache = load_ingest_cache()
            sources = _authorized_sources(load_indexed_sources())
            if not sources:
                sources = _authorized_sources(qdrant_list_sources())
            sources = sorted(
                [source for source in sources if "quiz" in source.casefold() or "pharmtech" in source.casefold()],
                key=str.casefold,
            )
            rows = []
            for source in sources:
                values = _extract_inline_label_values(_source_texts(source, cache))
                count = sum(1 for label in values if re.match(r"Question\s+\d+\s*:", label, re.IGNORECASE))
                date_match = re.search(r"20\d{2}[-_]\d{2}[-_]\d{2}", source)
                date = _quiz_date_from_texts(_source_texts(source, cache))
                rows.append((source, date if date != "unknown" else (date_match.group(0).replace("_", "-") if date_match else "unknown"), count))
            answer_text = "| filename | date | number of questions |\n| --- | --- | --- |\n" + "\n".join(
                f"| {source} | {date} | {count} |" for source, date, count in rows
            ) if rows else "No quiz questions were found in the archive."
            record_query_audit(question, hit_count=sum(count for _source, _date, count in rows), outcome="quiz_question_inventory")
            trace_event(run_id, "quiz_question_inventory", "END", status="completed", answer=answer_text, records=rows)
            return answer_text
        records = _quiz_questions_from_sources(question)
        answer_text = _format_quiz_questions(records)
        record_query_audit(question, hit_count=sum(len(questions) for _source, questions in records), outcome="quiz_question_inventory")
        trace_event(run_id, "quiz_question_inventory", "END", status="completed", answer=answer_text, records=records)
        return answer_text

    def quiz_topic_source(question, plan, run_id):
        records = _quiz_sources_for_topic(question)
        answer_text = _format_quiz_topic_sources(records)
        record_query_audit(question, hit_count=len(records), outcome="quiz_topic_source")
        trace_event(run_id, "quiz_topic_source", "END", status="completed", answer=answer_text, records=records)
        return answer_text

    def source_by_doc_id(question, plan, run_id):
        sources = _source_for_doc_id(question)
        answer_text = _format_source_inventory(sources)
        record_query_audit(question, hit_count=len(sources), outcome="source_by_doc_id")
        trace_event(run_id, "source_by_doc_id", "END", status="completed", answer=answer_text, sources=sources)
        return answer_text

    def total_charges(question, plan, run_id):
        requested_date = _requested_service_date(question)
        totals = _total_charges_for_date(question)
        answer_text = _format_total_charges(totals, requested_date)
        record_query_audit(question, hit_count=len(totals), outcome="total_charge_inventory")
        trace_event(run_id, "total_charges", "END", status="completed", answer=answer_text, totals=totals, date=requested_date)
        return answer_text

    def total_charges_inventory(question, plan, run_id):
        totals = _total_charges_for_all_records()
        default_text = _format_total_charges_inventory(totals, plan.include_grand_total)
        sections = [(service_date, [f"${total}"]) for service_date, total in totals]
        if plan.include_grand_total:
            grand_total = _sum_total_charges(totals)
            if grand_total:
                sections = sections + [("Combined Total", [f"${grand_total}"])]
        answer_text = _dispatch_grouped_format(plan.output_format, sections, "Service Date", "Total Charges", default_text)
        record_query_audit(question, hit_count=len(totals), outcome="total_charge_inventory")
        trace_event(run_id, "total_charges_inventory", "END", status="completed", answer=answer_text, totals=totals)
        return answer_text

    def performed_services(question, plan, run_id):
        requested_date = _requested_service_date(question)
        services = _performed_services_for_date(question)
        answer_text = _format_performed_services(services, requested_date)
        record_query_audit(question, hit_count=len(services), outcome="performed_service_inventory")
        trace_event(run_id, "performed_services", "END", status="completed", answer=answer_text, services=services, date=requested_date)
        return answer_text

    def performed_services_inventory(question, plan, run_id):
        records = _performed_services_for_all_records()
        if plan.sort_by_cost:
            records = _sort_service_records_by_cost(records)
        diagram_formatters = {
            "markdown_table": lambda: _format_performed_services_markdown_table(records, plan.include_total_cost, plan.cost_first, plan.include_grand_total),
            "ascii_table": lambda: _format_performed_services_ascii_table(records, plan.include_total_cost, plan.cost_first),
            "flowchart": lambda: _format_performed_services_flowchart(records, plan.include_total_cost),
            "sequence_diagram": lambda: _format_performed_services_sequence_diagram(records, plan.include_total_cost),
            "component_diagram": lambda: _format_performed_services_component_diagram(records, plan.include_total_cost),
        }
        answer_text = diagram_formatters.get(
            plan.output_format, lambda: _format_performed_services_inventory(records, plan.include_total_cost)
        )()
        record_query_audit(question, hit_count=sum(len(record[1]) for record in records), outcome="performed_service_inventory")
        trace_event(run_id, "performed_services_inventory", "END", status="completed", answer=answer_text, records=records)
        return answer_text

    def not_performed_services_inventory(question, plan, run_id):
        if plan.sort_by_cost:
            records = _not_performed_services_with_costs()
            answer_text = _format_not_performed_services_with_costs(records)
        else:
            records = _not_performed_services_for_all_records()
            default_text = _format_not_performed_services(records)
            answer_text = _dispatch_grouped_format(plan.output_format, records, "Service Date", "Services Not Performed", default_text)
        record_query_audit(question, hit_count=sum(len(record[1]) for record in records), outcome="not_performed_services_inventory")
        trace_event(run_id, "not_performed_services_inventory", "END", status="completed", answer=answer_text, records=records)
        return answer_text

    def service_advisor_inventory(question, plan, run_id):
        records = _service_advisors_for_all_records()
        default_text = _format_service_advisors(records)
        sections = [(service_date, [advisor]) for service_date, advisor in records]
        answer_text = _dispatch_grouped_format(plan.output_format, sections, "Service Date", "Service Advisor", default_text)
        record_query_audit(question, hit_count=len(records), outcome="service_advisor_inventory")
        trace_event(run_id, "service_advisor_inventory", "END", status="completed", answer=answer_text, records=records)
        return answer_text

    def repair_cause_inventory(question, plan, run_id):
        requested_date = _requested_service_date(question) if plan.date_text else None
        records = _repair_causes_for_all_records(requested_date)
        default_text = _format_repair_causes(records)
        sections = [
            (service_date, [f"{repair}: {cause}" for repair, cause in causes])
            for service_date, causes in records
        ]
        answer_text = _dispatch_grouped_format(plan.output_format, sections, "Service Date", "Repair: Cause", default_text)
        record_query_audit(question, hit_count=sum(len(record[1]) for record in records), outcome="repair_cause_inventory")
        trace_event(run_id, "repair_cause_inventory", "END", status="completed", answer=answer_text, records=records)
        return answer_text

    def label_values_inventory(question, plan, run_id):
        named_sources = _named_source_candidates(question)
        if named_sources:
            records = _label_values_for_named_sources(named_sources)
            answer_text = _format_named_label_values(records, question)
            record_query_audit(question, hit_count=len(records), outcome="label_values_inventory")
            trace_event(run_id, "label_values_inventory", "END", status="completed", answer=answer_text, records=records)
            return answer_text
        requested_date = _requested_service_date(question) if plan.date_text else None
        default_labels = (
            "PROMISED",
            "READY",
            "PO NO",
            "RATE",
            "PAYMENT",
            "INV. DATE",
            "DEL. DATE",
            "PROD. DATE",
            "WARR. EXP",
            "R.O. OPENED",
        )
        labels = tuple(label.title() for label in plan.requested_fields) if plan.requested_fields else default_labels
        records = _label_values_for_all_records(labels, requested_date=requested_date)
        answer_text = _format_label_values(records, labels, requested_date=requested_date)
        record_query_audit(question, hit_count=len(records), outcome="label_values_inventory")
        trace_event(run_id, "label_values_inventory", "END", status="completed", answer=answer_text, records=records)
        return answer_text

    def broad_scope(question, plan, run_id):
        answer_text = _clarification_for_broad_query()
        record_query_audit(question, outcome="clarification_required")
        trace_event(run_id, "broad_scope", "END", status="clarification_required", answer=answer_text)
        return answer_text

    for intent, handler in {
        "source_inventory": source_inventory,
        "service_date_inventory": service_date_inventory,
        "document_date_inventory": document_date_inventory,
        "quiz_question_inventory": quiz_question_inventory,
        "quiz_topic_source": quiz_topic_source,
        "source_by_doc_id": source_by_doc_id,
        "total_charges": total_charges,
        "total_charges_inventory": total_charges_inventory,
        "performed_services": performed_services,
        "performed_services_inventory": performed_services_inventory,
        "not_performed_services_inventory": not_performed_services_inventory,
        "service_advisor_inventory": service_advisor_inventory,
        "repair_cause_inventory": repair_cause_inventory,
        "label_values_inventory": label_values_inventory,
        "broad_scope": broad_scope,
    }.items():
        registry.register(intent, handler)
    return registry


def _source_inventory_date(question: str) -> str | None:
    """Return a normalized date constraint from a source-inventory question."""
    return _requested_service_date(question)


def _filter_sources_by_date(sources: list[str], question: str) -> list[str]:
    """Filter filenames when a source-inventory question specifies a date."""
    requested_date = _source_inventory_date(question)
    if not requested_date:
        return sources
    return [source for source in sources if requested_date in source]


def _authorized_sources(sources):
    """Filter manifest-backed source names according to the active policy."""
    manifests = load_manifests(EVENT_MANIFEST_PATH)
    visible = authorized_event_ids(manifests)
    result = []
    for source in sources:
        manifest = find_manifest_for_source(manifests, source)
        if is_source_authorized(manifest) and (manifest is None or manifest.event_id in visible):
            result.append(source)
    return result


def _filter_authorized_hits(hits):
    """Remove indexed chunks belonging to unauthorized manifest events."""
    manifests = load_manifests(EVENT_MANIFEST_PATH)
    visible = authorized_event_ids(manifests)
    return [
        hit for hit in hits
        if (
            is_source_authorized(
                find_manifest_for_source(manifests, str(hit.get("payload", {}).get("source", "")))
            )
            and (
                not hit.get("payload", {}).get("event_id")
                or hit.get("payload", {}).get("event_id") in visible
            )
        )
    ]


def _merge_hits(named_hits, semantic_hits, limit: int):
    """Place exact filename matches first and remove duplicate chunks."""
    merged = []
    seen = set()
    for hit in [*named_hits, *semantic_hits]:
        payload = hit.get("payload", {})
        key = hit.get("id", (payload.get("doc_id"), payload.get("page"), payload.get("chunk_index")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
    return merged[:limit]


def answer(question: str, top_k: int = 10, max_excerpt_chars: int = 1200, verbose: bool = False):
    """Answer a user question using semantic retrieval over the archive index.

    The function embeds the question, finds the nearest matching chunks in Qdrant,
    builds a prompt from the top excerpts, and asks the configured LLM to answer
    based only on that retrieved context.
    """
    start_total = time.perf_counter()
    validate_sensitive_configuration()
    query_plan = plan_query(question)
    runtime_route = route_query(question)
    run_id = new_run_id("query")
    trace_event(
        run_id,
        "query",
        "BEGIN",
        question=question,
        intent=query_plan.intent,
        requires_llm=query_plan.requires_llm,
        route=runtime_route,
    )
    if runtime_route == "broad_scope" and query_plan.intent == "free_text":
        return _clarification_for_broad_query()
    handler = _deterministic_handlers().get(query_plan.intent)
    if handler is not None:
        return handler(question, query_plan, run_id)
    document_summary = query_plan.intent == "multi_event_summary" or runtime_route == "multi_document_summary"
    document_sources = []
    record_groups = []
    if document_summary:
        document_sources = load_indexed_sources()
        record_groups = _group_sources_into_records(document_sources)
        max_summary_documents = int(os.environ.get("MAX_SUMMARY_DOCUMENTS", "20"))
        if len(record_groups) > max_summary_documents:
            return (
                f"This request matches {len(record_groups)} indexed service records. "
                "Please narrow it with a filename, filename_regex=..., topic, date range, or document type."
            )
        print(
            f"[query] document_balanced_retrieval=True "
            f"source_count={len(document_sources)} record_count={len(record_groups)}"
        )

    q_emb_start = time.perf_counter()
    q_emb = ollama_embed_text(question)
    q_emb_elapsed = time.perf_counter() - q_emb_start
    print(f"[query] embedding_question_seconds={q_emb_elapsed:.2f} question_length={len(question)}")

    search_start = time.perf_counter()
    if document_summary:
        semantic_hits = _document_balanced_hits(q_emb, record_groups)
    else:
        res = qdrant_search(q_emb, top_k=top_k)
        semantic_hits = res.get("result", [])
    lexical_hits = [] if document_summary else lexical_search(
        question, load_ingest_cache(), SEARCHABLE_DIR, limit=top_k
    )
    named_hits = []
    for filename in _filename_candidates(question):
        named_hits.extend(qdrant_search_by_source(filename, limit=top_k))
    regex_hits = []
    indexed_sources = load_indexed_sources()
    for pattern in _filename_regex_candidates(question):
        try:
            source_regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return f"Invalid filename regex: {exc}"
        for source in indexed_sources:
            if source_regex.search(source):
                regex_hits.extend(qdrant_search_by_source(source, limit=top_k))
    hits = _merge_hits(
        [*named_hits, *regex_hits, *lexical_hits],
        semantic_hits,
        len(semantic_hits) if document_summary else top_k,
    )
    hits = _filter_authorized_hits(hits)
    search_elapsed = time.perf_counter() - search_start
    print(f"[query] qdrant_search_seconds={search_elapsed:.2f} top_k={top_k} hit_count={len(hits)}")

    if not hits:
        total_elapsed = time.perf_counter() - start_total
        print(f"[query] total_query_seconds={total_elapsed:.2f} no_hits=True")
        return "I couldn't find anything relevant in the archive."

    excerpts = _summarize_hits(hits, max_excerpt_chars=max_excerpt_chars)
    context = "\n\n".join(excerpts)
    trace_event(
        run_id,
        "retrieval",
        "END",
        status="completed",
        hit_count=len(hits),
        excerpts=excerpts,
        context_sent_to_model=context,
    )
    print(
        f"[query] excerpts_count={len(excerpts)} context_chars={len(context)} "
        f"max_excerpt_chars={max_excerpt_chars}"
    )

    if verbose:
        print("TOP HITS:")
        for i, h in enumerate(hits[:5]):
            payload = h.get("payload", {})
            print(
                i + 1,
                "page=",
                payload.get("page"),
                "chunk=",
                payload.get("chunk_index"),
            )
            print(
                str(payload.get("text", ""))[:400].replace("\n", " "),
                "\n---\n",
            )

    qwen_model = os.environ.get("ANSWER_MODEL", "gemma3:4b")

    if document_summary:
        record_hits = _hits_by_record(hits)
        summaries = []
        chat_start = time.perf_counter()
        for record_key, _sources in record_groups:
            scoped_hits = record_hits.get(record_key, [])
            if scoped_hits:
                facts = _record_facts(_sources)
                trace_event(
                    run_id,
                    "llm_input",
                    "BEGIN",
                    record_key=record_key,
                    extracted_facts=facts,
                    evidence_sent_to_model="\n\n".join(_summarize_hits(scoped_hits, max_excerpt_chars=max_excerpt_chars)),
                )
                summary = _summarize_one_record(
                    question,
                    record_key,
                    scoped_hits,
                    qwen_model,
                    max_excerpt_chars,
                    facts,
                )
                summary = _enforce_locked_facts(summary, facts)
                summaries.append(f"### {record_key}\n\n{summary}")
        answer_text = "\n\n".join(summaries)
        chat_elapsed = time.perf_counter() - chat_start
        total_elapsed = time.perf_counter() - start_total
        print(
            f"[query] llm_record_calls={len(summaries)} "
            f"llm_chat_seconds={chat_elapsed:.2f} total_query_seconds={total_elapsed:.2f} "
            f"model={qwen_model} answer_chars={len(answer_text)}"
        )
        record_query_audit(
            question,
            hit_count=len(hits),
            event_ids=[record_key for record_key, _sources in record_groups],
        )
        artifact_path = _save_report_artifact(question, hits, answer_text, qwen_model, top_k)
        if artifact_path:
            print(f"[query] report_artifact={artifact_path}")
        trace_event(run_id, "answer", "END", status="completed", answer=answer_text)
        return answer_text
    else:
        system = "You are a helpful assistant. Answer the user using ONLY the provided excerpts. If the answer is not in the excerpts, say you don't know."
    user = f"User question:\n{question}\n\nExcerpts:\n{context}\n\nAnswer in plain text:"

    chat_start = time.perf_counter()
    answer_text = ollama_chat(
        qwen_model,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        session=REQUEST_SESSION,
    )
    trace_event(run_id, "llm_input", "BEGIN", system_prompt=system, user_prompt=user)
    chat_elapsed = time.perf_counter() - chat_start
    total_elapsed = time.perf_counter() - start_total
    print(
        f"[query] llm_chat_seconds={chat_elapsed:.2f} total_query_seconds={total_elapsed:.2f} "
        f"model={qwen_model} answer_chars={len(answer_text.strip())}"
    )
    record_query_audit(question, hit_count=len(hits))

    artifact_path = _save_report_artifact(question, hits, answer_text, qwen_model, top_k)
    if artifact_path:
        print(f"[query] report_artifact={artifact_path}")

    trace_event(run_id, "answer", "END", status="completed", answer=answer_text.strip())

    return answer_text.strip()


def main():
    """Command-line entry point for asking a question against the archive."""
    parser = argparse.ArgumentParser(description="Ask a grounded question about the archive.")
    parser.add_argument("question_positional", nargs="?", help="Question to answer from indexed archive evidence")
    parser.add_argument("--question", dest="question_named", help="Question to answer from indexed archive evidence")
    parser.add_argument("--verbose", action="store_true", help="Print the top retrieved excerpts")
    parser.add_argument("--top-k", type=int, default=10, help="Number of semantic results to retrieve")
    parser.add_argument("--save-report", action="store_true", help="Save a local Markdown report")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help=f"Report directory (default: {DEFAULT_ARTIFACT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    if args.question_positional and args.question_named:
        parser.error("provide the question either positionally or with --question, not both")
    question = args.question_named or args.question_positional
    if not question:
        parser.error("a question is required; use --question TEXT")

    if args.save_report:
        global SAVE_REPORT_ARTIFACT
        SAVE_REPORT_ARTIFACT = True
        os.environ["SAVE_REPORT_ARTIFACT"] = "1"
    if args.artifact_dir is not None:
        global ARTIFACT_OUTPUT_DIR
        ARTIFACT_OUTPUT_DIR = args.artifact_dir.expanduser().resolve()

    REPORT_ARTIFACT_WRITTEN = False
    out = answer(question, top_k=args.top_k, verbose=args.verbose)
    if args.save_report and not REPORT_ARTIFACT_WRITTEN:
        report_path = _save_report_artifact(
            question,
            [],
            out,
            os.environ.get("ANSWER_MODEL", "deterministic"),
            args.top_k,
        )
        if report_path:
            print(f"[query] report_artifact={report_path}")
    print(out)


if __name__ == "__main__":
    main()
