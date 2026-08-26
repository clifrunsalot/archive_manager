# Archive Manager Architecture

## Purpose

Archive Manager ingests multi-page documents, extracts searchable text, stores embeddings in Qdrant, and answers scoped questions with a local Ollama model. The architecture is event-oriented so one logical event can contain many pages and future domains can be added without changing the ingestion core.

Supported event domains include:

- `automotive_service`
- `tax`
- `medical`
- `insurance`
- `investment`
- `retirement`
- `banking_statement`
- `general_document`

## System Flow

```text
Input files
    |
    v
Intake manifest
    |
    v
Watcher or direct ingestion
    |
    +--> Normalize PDF or image
    +--> Searchability check
    +--> OCR when needed
    +--> Extract page-separated text
    +--> Chunk each page
    +--> Embed chunks with Ollama
    +--> Store vectors and metadata in Qdrant
    +--> Store ingest cache and derived files locally
    |
    v
Event-aware query
    |
    +--> Resolve authorized event scope
    +--> Retrieve relevant chunks or all pages for a manifest event
    +--> Parse deterministic domain facts
    +--> Ask Ollama to summarize evidence
    +--> Append validated fields and warnings
    +--> Write privacy-conscious audit record
    |
    v
Answer with provenance and uncertainty
```

## Project Layout

Implementation code lives under `src/archive_manager/`, split into subpackages
by responsibility rather than as a flat file list:

```text
src/archive_manager/
    core/        event_model.py, event_manifests.py, event_facts.py, encryption.py
    config/      settings_loader.py
    ingestion/   ingest.py, intake.py, ocr_adapters.py, watch_archive.py
    retrieval/   query.py, query_planner.py, query_handlers.py, hybrid_retrieval.py
    domain/      automotive_parser.py, domain_parsers.py, fact_validation.py,
                 arithmetic.py, response_validation.py
    security/    access_policy.py
    lifecycle/   retention.py, audit.py, trace_log.py
    admin/       delete_event.py, purge_expired.py, reset_archive.py,
                 backfill_event_facts.py
    evaluation/  evaluation.py, evaluate_queries.py
```

`paths.py` at the package root defines the single `PROJECT_ROOT` used to
locate `ARCHIVE/`, `data/`, and `logs/`; modules import it rather than
re-deriving it from their own file location. `pip install -e .` registers
console-script entry points (e.g. `archive-ingest`, `archive-query`) as the
only way to run the commands referenced throughout this document and the
README — there are no standalone scripts at the repo root. Tests live under
`tests/<group>/`, mirroring the same subpackage names.

## Core Concepts

### Event

An event is the logical record being queried. It may contain one page or many pages from several image files.

Examples:

```text
repair-2025-11-24
bank-statement-2026-01
medical-visit-2026-08-19
```

An event is represented by `EventManifest` in `event_model.py` and persisted by `event_manifests.py`.

### Page

A page is an uploaded source file within an event. Page metadata includes:

- Original source filename
- Page number
- Expected page count

Pages are ordered by manifest metadata. Unknown page order is retained and sorted after pages with known numbers.

### Document

A document is the content-addressed ingestion unit. Its ID is the SHA-256 hash of the input file. A document may be an image or PDF and can produce a normalized PDF, OCR sidecar, and several embedded chunks.

### Chunk

A chunk is the embedding unit stored in Qdrant. Existing payloads retain these compatibility fields:

```json
{
  "doc_id": "sha256...",
  "source": "page-001.jpg",
  "page": 1,
  "chunk_index": 0,
  "text": "..."
}
```

Manifest-backed chunks additionally contain:

```json
{
  "event_id": "repair-2025-11-24",
  "event_type": "automotive_service",
  "subject_ref": "subject-opaque-001",
  "event_page_number": 1,
  "event_page_count": 4
}
```

## Intake and Grouping

`intake.py` creates a manifest for a group of files:

```bash
./.venv/bin/archive-intake \
  --event-id repair-2025-11-24 \
  --event-type automotive_service \
  page-001.jpg page-002.jpg page-003.jpg page-004.jpg
```

The manifest is written to `data/events.json` by default. The filesystem watcher remains non-interactive and continues to support legacy files that have no manifest.

A manifest is authoritative for grouped pages. The query engine retrieves all indexed chunks from each manifest page, ordered by page metadata, rather than relying only on semantic top-k retrieval.

## Ingestion Layer

The ingestion pipeline is implemented primarily in `ingest.py`:

1. Calculate a stable SHA-256 document ID.
2. Skip a document already recorded in the ingest cache.
3. Convert images to PDFs when necessary.
4. Detect whether the PDF already contains enough text.
5. Run PaddleOCR for scanned content.
6. Split OCR output into pages using form-feed boundaries.
7. Chunk each page with configurable character size and overlap.
8. Embed chunks through Ollama.
9. Upsert vectors and payload metadata into Qdrant.
10. Record the source in `data/.ingest_cache/ingested.json`.

The watcher in `watch_archive.py` only detects files, waits for file stability, and submits ingestion jobs. It does not prompt for metadata. Grouping belongs in `intake.py` or a future manifest import workflow.

### OCR Backend Migration Boundary

`ocr_adapters.py` defines the migration seam for OCR engines. `OCRRequest` carries
the normalized PDF, output sidecar, storage directories, image/model settings,
and timeout. The backend must write page-separated UTF-8 text to the existing
`data/searchable/<document-id>.txt` contract. `ingest.py` retains
`run_ocr_docker()` as a compatibility wrapper, so existing callers and tests do
not change when a backend is added.

Backends may also write a parallel structured artifact at
`data/searchable/<document-id>.ocr.json` with page elements, recognized text,
confidence, and bounding boxes. The text sidecar remains the compatibility
artifact; the structured artifact is the basis for future layout-aware
label/value and table extraction.

The current registry contains only `paddleocr`. A future Docling adapter should
first pass sidecar parity tests against the same document fixtures, then be
enabled with `OCR_ENGINE=docling`. Existing PaddleOCR remains the immediate
rollback option.

### Persistent OCR Service

`docker-compose.yml` runs the `paddleocr` container in `--serve` mode
(`ocr/paddleocr_runner.py`), which loads the PaddleOCR model once at startup
and exposes it over HTTP on `http://localhost:8000`. `PaddleOCRBackend.run()`
posts each document to this service first; the model stays resident in memory
across documents instead of being reloaded per document, which was the
dominant cost of the previous one-shot `docker run --rm` invocation. If the
service is unreachable, `run()` transparently falls back to the original
`docker run --rm` one-shot container (`build_command()`), so ingestion still
works without the persistent service running.

## Query Layer

`query.py` is the runtime orchestration layer for archive questions. The
preferred pattern is a small routing policy, not an expanding business-specific
intent catalog.

The active routing abstraction is a compact policy with categories such as:

- `deterministic_utility`
- `constrained_exact_query`
- `multi_document_summary`
- `broad_scope`
- `rag`

This policy is implemented in `query_planner.py` and is intentionally smaller
than the older automotive/service-specific intent taxonomy. The legacy
`plan_query()` function remains as a compatibility adapter for exact deterministic
queries and older tests, but the migration goal is to treat routing as the main
boundary and keep domain-specific intent expansion out of the planner.

The runtime flow is:

1. route the question to one of the small policy buckets
2. enforce authorization and manifest scoping before retrieval
3. retrieve relevant evidence using lexical + dense + exact source matches
4. group manifest-backed records when available and preserve page ordering
5. synthesize the answer from retrieved excerpts only
6. optionally validate or enrich with parser/EventFacts metadata if useful

Deterministic dispatch remains in the `QueryHandlerRegistry` in
`query_handlers.py`, but only for a small closed-world set of operations such as:

- authorization-aware source inventory
- exact numeric or reporting queries that must remain non-LLM
- manifest and event lookup
- scope narrowing and clarification
- admin and lifecycle utilities

General content questions default to hybrid retrieval and grounded synthesis.
Filename and filename-regex matches remain high-priority evidence sources, and
manifest-backed events are grouped before synthesis so page ordering and event
bounds remain intact.

This is the key architectural shift: parsers, EventFacts, and domain-specific
logic are optional enrichment layers, not the default answer path. New document
families should still be answerable through retrieval even before a dedicated
parser exists.
- Event domains select deterministic parsers.
- EventFacts are extracted during ingestion and reused by manifest-backed queries.
- Arithmetic reconciliation compares parsed line totals with declared invoice totals.
- Model summaries are checked against validated facts and excluded-work rules.
- The LLM summarizes supplied evidence but does not determine event membership.
- Validated facts and model-validation warnings are appended after the model response.

`evaluation.py` and `evaluate_queries.py` provide fixture-driven checks for
planner intent, answer substrings, and query latency.

`trace_log.py` writes the cross-script processing trace to
`logs/archive_trace.jsonl`. A shared `run_id` correlates `BEGIN`/`END` stage
records. Query records include the question, parsed plan, evidence and facts
sent to the model, and final answer; ingestion records cover normalization, OCR,
text extraction, embedding, Qdrant upsert, EventFacts, and completion.

## Runtime Settings

`settings.env` provides non-secret defaults and is loaded automatically before
ingestion/query configuration is initialized. Existing shell variables take
precedence; set `ARCHIVE_SETTINGS_FILE` to use another settings file. Ollama
sampling and context settings include temperature, seed, top-p, top-k, and
context length. Secrets such as Qdrant API keys and Fernet keys must remain in
the shell or a protected secret store, not in `settings.env`.

For automotive records, `automotive_parser.py` extracts VINs, dates, invoice-level totals, alternate total labels, and warnings across all event pages.

For tax, medical, insurance, investment, retirement, and banking records, `domain_parsers.py` currently extracts only explicitly labeled metadata with page and confidence information. It does not infer transactions, balances, diagnoses, tax amounts, or other unsupported values.

## Domain Parser Boundary

Domain parsers implement a common shape:

```text
pages -> ParseResult

ParseResult:
  domain
  fields[]
  warnings[]

ExtractedField:
  name
  value
  page
  confidence
```

The registry in `domain_parsers.py` allows new domains to be added without changing the core file watcher, OCR, chunking, embedding, or Qdrant code.

### EventFacts

Manifest-backed events are parsed after successful indexing and persisted in
`data/.event_facts/facts.json`. `EventFacts` contains the selected domain,
validated fields, performed automotive services, totals, warnings, and source
page references. Queries use these cached facts first and retain OCR parsing as
a fallback for legacy events or records ingested before EventFacts existed.

The facts store uses the same optional Fernet metadata encryption as manifests.
Scoped event deletion and full reset remove EventFacts alongside source files,
vectors, cache entries, and manifests.

Full reset also clears generated logs, including ingest logs, query audit logs,
and the unified processing trace. Use `--no-logs` only when intentionally
preserving those records.

Automotive parsing remains specialized because invoice totals and service lines require cross-page reconciliation and OCR-aware label handling.

## Privacy and Security Controls

### Authorization

`access_policy.py` supports two modes:

- `compat`: preserves access to legacy and unclassified records.
- `strict`: requires a manifest `metadata.allowed_users` list to contain `ARCHIVE_AUDIT_USER`.

Strict mode filters query results and source inventories and blocks unauthorized event deletion.

```bash
export ARCHIVE_AUTH_MODE=strict
export ARCHIVE_AUDIT_USER=alice
```

### Qdrant Authentication

Set `QDRANT_API_KEY` for Qdrant client authentication and configure the same value for the Qdrant service in Compose.

```bash
export QDRANT_API_KEY=local-secret
```

### Manifest Encryption

When `ARCHIVE_ENCRYPTION_KEY` is configured, manifests are encrypted with Fernet authenticated encryption. Existing plaintext manifests remain readable until rewritten. Encrypted manifests fail closed when the key is absent or invalid.

```bash
export ARCHIVE_ENCRYPTION_KEY=$(./.venv/bin/python -c 'from archive_manager.core.encryption import generate_key; print(generate_key())')
```

The key must be stored outside the repository. Losing the key makes encrypted metadata unrecoverable.

### Audit Logging

`audit.py` appends JSON Lines records to `logs/query_audit.jsonl`. Entries contain:

- Timestamp
- Configured local user
- SHA-256 hash of the question
- Hit count
- Event IDs
- Outcome

Raw questions and model answers are not written to the audit log.

### Deletion and Retention

`delete_event.py` removes one event's Qdrant points, source files, generated files, cache entries, and manifest entry. It supports dry-run and confirmation.

```bash
./.venv/bin/archive-delete-event event-id --dry-run
./.venv/bin/archive-delete-event event-id
```

Retention is opt-in. A manifest can specify `expires_at`, or `created_at` plus `retention_days`. Events without an explicit policy are never automatically purged.

```bash
./.venv/bin/archive-purge-expired --dry-run
```

Purge reuses event authorization and scoped deletion.

## Storage Layout

```text
ARCHIVE/                         Original incoming files
 data/source/                    Normalized PDFs
 data/searchable/                OCR text and searchable PDFs
 data/.ingest_cache/ingested.json
 data/events.json                Event manifests
 data/.event_facts/facts.json    Ingest-time extracted facts
 logs/                            Ingest and query audit logs
 Qdrant archive_chunks collection
 Ollama local model and embedding services
```

Sensitive identifiers should not be placed in filenames. Prefer opaque event IDs and subject references in manifests and storage metadata.

## Compatibility Strategy

The implementation is additive:

- Existing `doc_id`, `source`, `page`, `chunk_index`, and `text` payload fields remain.
- Legacy files without manifests continue to ingest and query.
- The existing ingest cache format remains compatible.
- New event metadata is added only when a manifest matches the source filename.
- The watcher remains non-interactive.

## Current Limitations

The following work remains before treating the system as suitable for production PII, PHI, or financial records:

- Encrypt OCR sidecars, normalized PDFs, backups, and Qdrant storage at rest.
- Add a durable identity and access-management system instead of environment-based local users.
- Reprocess older manifest events so they receive EventFacts records.
- Add deeper transaction, balance, tax, insurance, and medical schemas with domain fixtures and review rules.
- Harden container images, dependency versions, network exposure, and secret management.
- Define operational retention, backup, recovery, and incident-response procedures.

This architecture provides extensibility and local safeguards, but it does not by itself establish HIPAA, GDPR, tax, or other regulatory compliance.
