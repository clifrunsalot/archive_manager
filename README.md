# Archive Processing Pipeline

Security hardening tasks are tracked in [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md).

All document processing and storage is **sensitive by default** (`ARCHIVE_SECURITY_MODE=sensitive`).
Fail-closed security checks, manifest encryption at rest (using auto-generated `.archive_key`),
trace log redaction, local service endpoint validation, and payload minimization are active automatically.

### Toggling Security Modes

- **Turn OFF sensitive mode** (compatibility mode for legacy / unclassified work):
  ```bash
  source scripts/sensitive-off.sh
  ```
- **Turn ON sensitive mode** (re-enable sensitive protections & strict authorization):
  ```bash
  source scripts/sensitive-on.sh
  ```
- **Run a single command in sensitive mode**:
  ```bash
  ./scripts/sensitive python -m archive_manager.ingestion.ingest
  ```

Apply owner-only permissions to local sensitive storage with:

```bash
./scripts/secure-permissions.sh
```

Remove generated logs and report artifacts before sensitive ingestion with the
confirmation-gated command:

```bash
./scripts/secure-cleanup.sh
```

This project ingests documents into a searchable archive and supports semantic retrieval over the stored content. The pipeline watches a directory for new files, normalizes them into a usable PDF form, extracts text, chunks it, embeds the content, and stores both vector embeddings and source metadata in Qdrant.

## Start Locally

Use three terminals. Run every command from the repository unless the command
changes directory itself. The local UI is available at `http://127.0.0.1:5173/`.

### `settings.env`

Create `settings.env` before starting the tool. It must contain the local
service settings, including `QDRANT_API_KEY` and `ARCHIVE_ENCRYPTION_KEY`.
Keep it out of version control and retain the encryption key; changing it makes
existing encrypted manifests unreadable. The API startup command below loads
this file, and Docker Compose receives it through `--env-file settings.env`.

### 1. Start local dependencies

Start Docker Desktop, then run:

```bash
cd /Users/cliftonhudson/archive_manager
docker compose --env-file settings.env up -d qdrant ollama paddleocr
docker compose --env-file settings.env ps
```

Wait until Qdrant, Ollama, and PaddleOCR are running. On the first startup,
download the required Ollama models:

```bash
docker compose --env-file settings.env up ollama-models
```

### 2. Start the API

In a second terminal:

```bash
cd /Users/cliftonhudson/archive_manager
source .venv/bin/activate
set -a
source settings.env
set +a
ARCHIVE_AUTH_MODE=strict ARCHIVE_LOCAL_ONLY=1 archive-api
```

Leave this terminal running. Confirm the API is ready:

```bash
curl -sS http://127.0.0.1:8080/api/ready
```

### 3. Start the browser UI

In a third terminal:

```bash
cd /Users/cliftonhudson/archive_manager/web
VITE_API_MODE=live npm run dev -- --host 127.0.0.1
```

Open `http://127.0.0.1:5173/` in a browser.

### Stop Local Services

Stop the API and Vite terminals with `Ctrl+C`, then stop Docker services:

```bash
cd /Users/cliftonhudson/archive_manager
docker compose --env-file settings.env down
```

Do not add `-v`; it removes persisted Qdrant data.

The **Activity & Logs** view shows sanitized query audit entries plus the
authenticated user's ingestion and lifecycle job statuses. It does not expose
raw OCR text, source documents, credentials, or unfiltered log files.

The Event Catalog also includes an **Artifacts in archive** list backed by the
processed ingest cache. It is fetched from `/api/v1/artifacts` when the catalog
opens, so it reflects newly ingested files and becomes empty after an archive
reset.

The local workflow is loopback-only and derives the username from the operating
system account. Follow [Start Locally](#start-locally); it is the single
supported browser startup procedure. LAN/OIDC deployment is deferred to
[FUTURE_LAN_OIDC_UPGRADE.md](FUTURE_LAN_OIDC_UPGRADE.md).

## API middle layer

The FastAPI middle layer is available through the `archive-api` command. The
shared query, catalog, intake, lifecycle, and security services back both the
API and UI integrations.

The server listens on `http://127.0.0.1:8080` by default. Its local startup
command and required environment are listed in [Start Locally](#start-locally).

`GET /api/health` is a lightweight liveness check. `GET /api/ready` checks
Qdrant, Ollama, and PaddleOCR with short timeouts and returns `503` while any
dependency is unavailable; it does not expose service URLs or credentials.

The Compose API, OAuth2 Proxy, and Caddy services are deployment scaffolding,
not part of the local startup path. See
[FUTURE_LAN_OIDC_UPGRADE.md](FUTURE_LAN_OIDC_UPGRADE.md) before using them.

It also supports an optional local report-export mode for query results. When enabled, the system saves a clean Markdown report to a local output directory such as `artifact_output`, using only the retrieved evidence that supported the answer. This keeps the feature opt-in and safe for local-only use.

## Configuration

The local startup procedure is [Start Locally](#start-locally). The settings
below are reference values for advanced configuration only.

These values are optional if you are using the defaults, but they are the correct variables to set when overriding the local configuration:

```bash
export QDRANT_URL=http://localhost:6333
export QDRANT_COLLECTION=archive_chunks
export QDRANT_HNSW_EF=64
export QDRANT_API_KEY=replace-with-a-local-secret
export OLLAMA_BASE=http://localhost:11434
export EMBED_MODEL=nomic-embed-text:latest
export ANSWER_MODEL=gemma3:4b
export OLLAMA_TEMPERATURE=0
export OLLAMA_SEED=42
export OLLAMA_TOP_P=0.2
export OLLAMA_TOP_K=20
export OLLAMA_NUM_CTX=16384
export PADDLEOCR_IMAGE=archive-paddleocr:latest
export OCR_ENGINE=paddleocr
```

Storage locations default to paths under the project root and can be
overridden individually if needed:

```bash
export ARCHIVE_DIR=/path/to/incoming
export SOURCE_DIR=/path/to/data/source
export SEARCHABLE_DIR=/path/to/data/searchable
export CACHE_DIR=/path/to/data/.ingest_cache
export LOGS_DIR=/path/to/logs
```

OCR selection is isolated behind a backend adapter. `paddleocr` is currently the
only implemented backend; keep `OCR_ENGINE=paddleocr` during migration. A future
backend must produce the same page-separated UTF-8 sidecar contract before it
can be enabled:

```text
data/searchable/<document-id>.txt
page 1 text
\f
page 2 text
```

The ingestion, chunking, embedding, and Qdrant layers do not depend on the OCR
implementation. An unsupported engine fails immediately with a list of supported
engines rather than silently falling back.

For sensitive metadata, generate and store a Fernet key outside the repository,
then export it before using the archive:

```bash
export ARCHIVE_ENCRYPTION_KEY=$(./.venv/bin/python -c 'from archive_manager.core.encryption import generate_key; print(generate_key())')
```

When configured, event manifests are encrypted at rest and cannot be read
without the key. Existing plaintext manifests remain compatible until they are
rewritten. Keep this key in a protected secret store; losing it makes encrypted
metadata unrecoverable.

The environment variables are optional because these are the project defaults. Set them in the terminal where you run the Python scripts if you need to override those defaults.

Local defaults and secrets are stored in the untracked `settings.env` file.
Load it before starting the API as shown in [Start Locally](#start-locally).
Existing shell exports take precedence. Use
`ARCHIVE_SETTINGS_FILE=/path/to/settings.env` to load a different settings file.

Ollama model settings are:

- `OLLAMA_TEMPERATURE`: randomness; use `0` for extraction
- `OLLAMA_SEED`: repeatability seed
- `OLLAMA_TOP_P`: nucleus sampling limit
- `OLLAMA_TOP_K`: token candidate limit
- `OLLAMA_NUM_CTX`: model context window

Qdrant creates a keyword index on the `source` payload for filename filtering.
It also indexes `event_id`, `event_type`, and `subject_ref` for event-scoped
filtering. Compose binds Qdrant and Ollama to localhost by default; do not expose
these ports publicly without adding authentication and a network boundary.
`QDRANT_HNSW_EF` controls the vector-search effort: increase it for recall on
larger collections, or decrease it for lower latency.

## PaddleOCR Dockerfile

The [ocr/Dockerfile](ocr/Dockerfile) defines the local PaddleOCR image used by
the ingestion pipeline. It uses Python 3.12, installs the PaddleOCR and
PaddlePaddle packages, includes the system libraries required for document and
image processing, and copies in the project OCR runner.

Docker Compose builds this Dockerfile automatically through the `paddleocr`
service in step 1 of [Start Locally](#start-locally).

To build the image without starting the Compose services, run:

```bash
docker build -t archive-paddleocr:latest ./ocr
```

### Persistent OCR service

The `paddleocr` Compose service runs [ocr/paddleocr_runner.py](ocr/paddleocr_runner.py)
in `--serve` mode, which loads the PaddleOCR model once at container startup and
keeps it resident in memory, listening on `http://localhost:8000`. Ingestion
posts each document to this service instead of starting a new container per
document, avoiding the model-load cost (previously incurred on every OCR call)
on every subsequent document.

Override the service URL with `PADDLEOCR_SERVICE_URL` (default
`http://localhost:8000`). If the service is unreachable — for example, when
running `ingest.py` without Docker Compose up — ingestion automatically falls
back to a one-shot `docker run --rm` container, matching the previous
behavior, so no separate configuration is required to make ingestion work
without the persistent service.

## OCR engine comparison

PaddleOCR is the supported OCR engine for this project. It is designed for scanned documents, multilingual text, and structured layouts.

| Tool | License | Strengths | Tradeoffs | Best use | Recommendation |
| --- | --- | --- | --- | --- | --- |
| PaddleOCR | Open source | High OCR accuracy on scanned PDFs, tables, multilingual text, and structured documents; strong local deployment options | Heavier runtime and memory use; better with GPU | Primary OCR engine for this project | Best choice |
| EasyOCR | Open source | Easy to run and useful for quick experiments | Less consistent on dense scanned PDFs and complex layouts than PaddleOCR | Alternative local OCR option | Not used by default |

Paid OCR services are not required for this local, open-source setup.

### Current implementation detail

The OCR step is centralized in `run_ocr_docker()` inside [ingest.py](ingest.py).
The container invokes PaddleOCR through `ocr/paddleocr_runner.py`, writes
page-separated text to `data/searchable/<document-id>.txt` and structured OCR
elements to `data/searchable/<document-id>.ocr.json`. The JSON artifact preserves
recognized text, confidence, page number, and bounding boxes when PaddleOCR
provides them, allowing future label/value and table parsing without breaking
the text-sidecar contract. The ingest pipeline indexes the text alongside the
original PDF. PaddleOCR model files
are stored in the `paddleocr_models` Docker volume so subsequent documents do
not download the models again.

The watcher processes one document at a time by default because PaddleOCR is
CPU- and memory-intensive. Set `ARCHIVE_INGEST_WORKERS` to a higher value only
when the host has enough resources for concurrent OCR containers. Each OCR run
has a 15-minute timeout, configurable with `OCR_TIMEOUT_SECONDS`. PDF pages are
rendered with a 3,000-pixel maximum side by default to prevent the OCR
container from exhausting memory; override this with `OCR_RENDER_MAX_SIDE`.

## CLI Watcher Workflow

Use this workflow to ingest files through the filesystem watcher.

1. Start the watcher in a terminal after completing
   [Start Locally](#start-locally):

```bash
cd /Users/cliftonhudson/archive_manager
source .venv/bin/activate
set -a
source settings.env
set +a
archive-watch
```

2. Copy finished files into `ARCHIVE/` from another terminal:

```bash
cp /path/to/document.pdf /Users/cliftonhudson/archive_manager/ARCHIVE/
```

   Supported extensions are `.pdf`, `.png`, `.jpg`, and `.jpeg`.

3. Leave each file in `ARCHIVE/`. The watcher waits for it to stop changing,
   then ingests it. Monitor the watcher terminal for progress or errors.

<!-- Superseded CLI alternatives retained temporarily for historical reference.

Run the command against an existing file path, without copying it into
`ARCHIVE`:

```bash
archive-ingest /path/to/document.pdf
```

Direct ingestion is useful for a one-off file. It does not create an event
manifest automatically, so it is treated as a legacy ungrouped document unless
you create a manifest separately.

#### Group pages into an event

Use this workflow when several image files or single-page PDFs are pages of one
logical item, such as a four-page repair invoice, a bank statement, or a medical
visit record. The manifest groups the files; it does not combine their pixels
into one file.

For the convenient interactive workflow, first stop the watcher if it is
running, then run:

```bash
archive-intake add
```

The command prompts for a file or directory path, detects supported files,
sorts numeric page names such as `page-1`, `page-2`, and `page-10` naturally,
shows the detected order, and asks you to confirm it. It then prompts for the
event type, an optional event ID, and an optional opaque subject reference.
Provide a directory for a multi-page event or a single file for a one-page
event. The manifest is written only after you confirm the detected order.

You can also provide the path as an argument while retaining the prompts for
metadata and confirmation:

```bash
archive-intake add --path /Users/cliftonhudson/archive_manager/ARCHIVE/repair-2025-11-24
```

The positional path form remains supported.

1. If the watcher is running, stop it temporarily with `Ctrl+C`. This prevents
	a page from being indexed before its event metadata exists. Put all pages in
	`ARCHIVE` and choose their order. Use clear filenames that
	make the order easy to verify, for example:

	```text
	ARCHIVE/repair-2025-11-24__page-001-of-004.jpg
	ARCHIVE/repair-2025-11-24__page-002-of-004.jpg
	ARCHIVE/repair-2025-11-24__page-003-of-004.jpg
	ARCHIVE/repair-2025-11-24__page-004-of-004.jpg
	```

2. Make sure every file has finished copying before creating the manifest. The
	current intake command stores the filenames as supplied and does not move,
	rename, or verify the files for you.

3. Create the manifest using the files in their intended page order:

```bash
archive-intake \
	--event-id repair-2025-11-24 \
	--event-type automotive_service \
	/Users/cliftonhudson/archive_manager/ARCHIVE/repair-2025-11-24__page-001-of-004.jpg \
	/Users/cliftonhudson/archive_manager/ARCHIVE/repair-2025-11-24__page-002-of-004.jpg \
	/Users/cliftonhudson/archive_manager/ARCHIVE/repair-2025-11-24__page-003-of-004.jpg \
	/Users/cliftonhudson/archive_manager/ARCHIVE/repair-2025-11-24__page-004-of-004.jpg
```

The command stores only each file's basename in the manifest, so the names in
the manifest must exactly match the names that the watcher ingests.

4. Start the watcher again:

	```bash
	archive-watch
	```

	The watcher now sees the completed pages and ingestion attaches the event
	metadata to every indexed page.

Manifests are stored in `data/events.json`. Each page receives an event ID,
event type, page number, and page count in its indexed metadata. Multiple events
can share a VIN or subject while remaining separate event groups. Supported event
types include `automotive_service`, `tax`, `medical`, `insurance`, `investment`,
`retirement`, `banking_statement`, and `general_document`.

The filesystem watcher remains non-interactive. For reliable grouping, create
the manifest before restarting the watcher. If a page was already indexed
without a manifest, it must be re-ingested after the manifest is created to add
event metadata. Files not listed in a manifest continue to use the legacy
ingestion behavior.

#### Query the archive

```bash
archive-query --question "What does the archive say about this topic?"
```

The positional question form remains supported.

To list the unique filenames currently indexed, ask for the names of the
processed files. This metadata query does not invoke the embedding model or the
LLM:

```bash
archive-query "Generate the names of the files processed thus far"
```

The filename inventory is read from the successful-ingestion catalog in
`data/.ingest_cache/ingested.json`. A broad question such as
`Tell me about the archive` requests a narrower scope instead of selecting
arbitrary semantic matches. Narrow it with a filename, `filename_regex=...`,
topic, date range, or document type.

To remove one manifest event and its indexed/generated data, preview the
deletion first and then confirm it:

```bash
archive-delete-event --event-id repair-2025-11-24 --dry-run
archive-delete-event --event-id repair-2025-11-24
```

The positional event ID form remains supported.

Event deletion removes that event's Qdrant points, source files, generated PDFs
and OCR sidecars, ingest-cache entries, and manifest entry. It does not delete
unrelated events. The command requires confirmation unless `--force` is used.

Queries append privacy-conscious audit records to
`logs/query_audit.jsonl` by default. Records contain a hash of the question,
hit count, event IDs, outcome, and timestamp, but not the raw question or model
answer. Multi-event responses append validated VIN, service date, total, and
extraction warnings after the model summary so those fields cannot be silently
omitted or changed by the model.

Set `ARCHIVE_AUTH_MODE=strict` to require each manifest's `metadata.allowed_users`
list to include `ARCHIVE_AUDIT_USER`. This policy filters query results and source
inventories and blocks deletion by unauthorized users. The default `compat` mode
preserves access to existing unclassified records while policies are introduced.

Manifest domains select conservative parsers through the domain registry. Tax,
medical, insurance, investment, retirement, and banking parsers currently extract
explicitly labeled metadata with page numbers and confidence values; they do not
infer transactions, balances, diagnoses, or tax amounts. Automotive service
records continue to use the specialized cross-page parser.

When a manifest event is summarized, its `event_type` selects the corresponding
parser. Validated fields are included with their source page and confidence in
the model context and in the final response. Unrecognized domains use the safe
general-document parser and produce no inferred fields.

Retention is opt-in per event. Add either an ISO-8601 `expires_at` value or both
`created_at` and a non-negative `retention_days` value under manifest metadata:

```json
{"created_at": "2026-01-01T00:00:00Z", "retention_days": 365}
```

Preview governed events before deletion:

```bash
archive-purge-expired --dry-run
archive-purge-expired
```

Events without an explicit retention policy are never automatically purged.
Purge reuses event authorization and scoped deletion, so it removes the event's
vectors, source files, generated files, cache entries, and manifest entry.
Scoped deletion and full archive reset also remove persisted `EventFacts`
metadata, so extracted facts do not survive deletion of their source documents.

To search for a specific document by filename, include its complete name in the
question. Exact filename matches are added before semantic matches:

```bash
archive-query "What does IMG_0944.png contain?"
```

For a filename regular-expression search, use `filename_regex=` in the
question. Matching filenames are added before semantic matches:

```bash
archive-query "Summarize files matching filename_regex=IMG_09[0-9]+\\.png"
```

For a request such as `Summarize each car service record`, the query uses
record-balanced retrieval: related page images are grouped by their OCR invoice
identifier, then a bounded number of relevant chunks is selected from every
record instead of allowing one document to consume the entire result set. The
response is instructed to keep records separate, include total charges when
available, and order records by date when requested. By default,
archive-wide summaries are limited to 20 documents; override this with
`MAX_SUMMARY_DOCUMENTS` when the host and model can handle a larger context.
Each record is sent to the answer model in an isolated call and the results are
combined locally, preventing one long invoice from suppressing the other records.
When pages are listed in an event manifest, all indexed chunks from every page
are retrieved and passed to the automotive fact extractor. The extractor checks
common total labels such as `TOTAL CHARGES`, `TOTAL COSTS`, `TOTAL AMOUNT`, and
`AMOUNT DUE`, preferring an invoice-level total over `PLEASE PAY` or line totals.

The query layer is intentionally small and routing-driven. The runtime now
classifies questions into a compact policy such as deterministic utility,
constrained exact query, multi-document summary, broad-scope clarification, or
normal RAG retrieval. This keeps the planner stable as new document types appear
without forcing a new regex branch for every business-specific record family.

Exact reporting, source inventory, authorization gating, and manifest lookups
remain deterministic and are dispatched through the query handler registry. The
general case uses hybrid lexical + dense retrieval, exact filename matching,
manifest grouping, and grounded synthesis from retrieved excerpts only.

This design keeps the closed-world enforcement path small and predictable while
making retrieval the default answer path for most archive questions. Parsers and
persisted EventFacts remain available for validation and enrichment, but they are
not the default mechanism for answering general content questions.

### Graphical output formats

Questions about performed services by date default to a bulleted list, but can
also request other common ways of conveying the same data:

```bash
archive-query "Generate a 3-column summary table of the services performed on each date: date, summary, cost."
archive-query "Show a flowchart of the services performed on each date"
archive-query "Generate a sequence diagram of the services performed on each date"
archive-query "Generate a component diagram of the services performed on each date"
```

A question containing "table" or "column(s)" returns a GitHub-Flavored Markdown
table (`| col | col |` / `| --- | --- |`) by default, so it renders as an
actual table wherever Markdown is viewed, including the saved `--save-report`
artifact. Ask specifically for an "ascii table" to get the previous
fixed-width `+---+` plain-text table instead. Questions containing "flowchart"
or "process diagram"/"process flow", "sequence diagram", or "component
diagram" return the same underlying data as a
[Mermaid](https://mermaid.js.org/) code block (` ```mermaid `), which renders as
a diagram in GitHub, VS Code, and other Mermaid-aware Markdown viewers —
useful with `--save-report` for a diagram embedded in the saved report. This
formatting applies to performed-services, total-charges, not-performed-services,
service-advisor, and repair-cause inventory questions; `multi_event_summary`
and `label_values_inventory` still return plain text since they don't share
the same simple grouped-data shape.

#### Query and save a local Markdown report

```bash
archive-query "What does the archive say about this topic?" --save-report
```

#### Enable report export via environment variables

```bash
export SAVE_REPORT_ARTIFACT=1
export ARTIFACT_OUTPUT_DIR=artifact_output
archive-query "What does the archive say about this topic?"
```

#### Reset the archive data

```bash
archive-reset --force
```

Use `--dry-run` to preview the destructive actions without deleting anything.

### Stop the services

From the project directory, run:

```bash
docker compose down
```

-->

## Script invocation reference

Run commands from the project directory. The examples use the repository
virtual environment; `python` can be used instead when it is already activated:

```bash
cd /Users/cliftonhudson/archive_manager
source .venv/bin/activate
```

Every executable script supports both `-h` and `--help`.

### `archive-ingest`

```bash
./.venv/bin/archive-ingest --input /path/to/document.pdf
./.venv/bin/archive-ingest /path/to/document.pdf
./.venv/bin/archive-ingest --help
```

### `archive-query`

```bash
./.venv/bin/archive-query --question "What services were performed?"
./.venv/bin/archive-query "What services were performed?"
./.venv/bin/archive-query --question "Summarize the repair records" --top-k 20 --verbose --save-report
./.venv/bin/archive-query --help
```

### `archive-watch`

```bash
./.venv/bin/archive-watch
./.venv/bin/archive-watch --watch-dir /path/to/incoming --workers 2
./.venv/bin/archive-watch --help
```

When running in an interactive terminal, press `p` or Space to pause/resume
scheduling new files. In-flight ingestion jobs finish normally. Press `Ctrl+C`
to stop the watcher. When the watcher runs in the background or without a
terminal, keyboard shortcuts are unavailable and file watching continues
normally.

### `archive-intake`

Interactive intake supports a single file or a directory of pages:

```bash
./.venv/bin/archive-intake add
./.venv/bin/archive-intake add --path /path/to/page-set
./.venv/bin/archive-intake add /path/to/page-set
./.venv/bin/archive-intake add --help
```

Non-interactive named forms support repeated files or a directory:

```bash
./.venv/bin/archive-intake --event-id repair-2025-11-24 --event-type automotive_service --file page-001.jpg --file page-002.jpg
./.venv/bin/archive-intake --event-id bank-2026-01 --event-type banking_statement --directory /path/to/pages
./.venv/bin/archive-intake --event-id repair-2025-11-24 --event-type automotive_service --directory /path/to/pages --pattern '^repair-page-[0-9]+\\.jpg$'
```

`--pattern` is a regular expression matched against filenames in a directory.
It is case-insensitive, limited to 200 characters, and is applied before
natural page ordering. In interactive mode, provide it as an option or leave
it blank at the filename-regex prompt to include all supported files:

```bash
./.venv/bin/archive-intake add --path /path/to/pages --pattern '^repair-page-[0-9]+\\.jpg$'
```

The original positional multi-file form remains supported:

```bash
./.venv/bin/archive-intake --event-id repair-2025-11-24 --event-type automotive_service page-001.jpg page-002.jpg
./.venv/bin/archive-intake --help
```

### `archive-delete-event`

Preview before deleting an event:

```bash
./.venv/bin/archive-delete-event --event-id repair-2025-11-24 --dry-run
```

Named and positional deletion forms:

```bash
./.venv/bin/archive-delete-event --event-id repair-2025-11-24
./.venv/bin/archive-delete-event repair-2025-11-24
./.venv/bin/archive-delete-event --event-id repair-2025-11-24 --force
./.venv/bin/archive-delete-event --event-id repair-2025-11-24 --qdrant-url http://localhost:6333 --collection archive_chunks --dry-run
./.venv/bin/archive-delete-event --help
```

### `archive-purge-expired`

```bash
./.venv/bin/archive-purge-expired --dry-run
./.venv/bin/archive-purge-expired
./.venv/bin/archive-purge-expired --force
./.venv/bin/archive-purge-expired --help
```

Only events with an explicit expired retention policy are eligible.

Backfill EventFacts for existing manifest events after this migration:

```bash
./.venv/bin/archive-backfill-event-facts --dry-run
./.venv/bin/archive-backfill-event-facts
./.venv/bin/archive-backfill-event-facts --event-id repair-2024-09-12
./.venv/bin/archive-backfill-event-facts --help
```

### `archive-reset`

Preview or perform a complete reset:

```bash
./.venv/bin/archive-reset --dry-run
./.venv/bin/archive-reset
./.venv/bin/archive-reset --force
```

Skip selected storage areas or override Qdrant settings:

```bash
./.venv/bin/archive-reset --dry-run --no-qdrant --no-cache --no-archive --no-source --no-searchable
./.venv/bin/archive-reset --qdrant-url http://localhost:6333 --collection archive_chunks --dry-run
./.venv/bin/archive-reset --help
```

By default, reset also deletes `data/events.json` and persisted EventFacts. Use
`--no-manifest`, `--no-event-facts`, or `--no-logs` only when intentionally
preserving those generated stores. Logs include per-ingest logs, query audit
records, and the unified archive trace.

### `archive-evaluate-queries`

Run the fixture-driven query regression evaluation:

```bash
./.venv/bin/archive-evaluate-queries --fixture evaluation_cases.json
./.venv/bin/archive-evaluate-queries --help
```

The evaluator reports intent accuracy, expected-answer substring matches, and
per-case latency. Add representative questions to `evaluation_cases.json` when
new domains or query handlers are introduced.

### Unified processing trace

All query and ingestion invocations write correlated stage records to
`logs/archive_trace.jsonl` by default. Override the location with
`ARCHIVE_TRACE_LOG`.

Each JSON Lines record contains a shared `run_id`, a `stage`, a `BEGIN` or `END`
boundary, a status, and stage-specific details. Query traces include the
question and planner intent, retrieved excerpts, extracted facts/evidence sent
to the model, and the final answer. Deterministic answers record the parsed
elements and answer with no LLM input stage. Ingestion traces include
normalization, OCR, text extraction, embedding, Qdrant upsert, EventFacts, and
completion stages.

The trace intentionally contains questions, OCR-derived values, model context,
and answers for debugging. Treat it as sensitive when processing medical, tax,
financial, or other personally identifiable information.

## Project files

The implementation lives in the installable `archive_manager` package under
`src/archive_manager/`, organized by responsibility:

- `core/`: event model, manifests, event facts, encryption
- `config/`: settings loader
- `ingestion/`: `ingest.py`, `intake.py`, `ocr_adapters.py`, `watch_archive.py`
- `retrieval/`: `query.py`, `query_planner.py`, `query_handlers.py`, `hybrid_retrieval.py`
- `domain/`: domain-specific parsers and validation (automotive, tax, medical, etc.)
- `security/`: `access_policy.py`
- `lifecycle/`: `retention.py`, `audit.py`, `trace_log.py`
- `admin/`: `delete_event.py`, `purge_expired.py`, `reset_archive.py`, `backfill_event_facts.py`
- `evaluation/`: `evaluation.py`, `evaluate_queries.py`

The repo root has no standalone CLI scripts; `pip install -e .` registers
console scripts (`archive-ingest`, `archive-query`, `archive-watch`,
`archive-intake`, `archive-delete-event`, `archive-purge-expired`,
`archive-backfill-event-facts`, `archive-reset`, `archive-evaluate-queries`) as
the single way to run every command in this README. Tests live in
`tests/<group>/`, mirroring the same package layout.

## Workflow

Files placed in `ARCHIVE` are stabilized by the watcher, converted to PDF when
needed, processed with PaddleOCR when they are not searchable, and indexed in
Qdrant with Ollama embeddings. Queries answer from retrieved evidence only.

Report export is disabled by default for privacy. When enabled, the default
output directory is the project-local `artifact_output/` directory, regardless
of the shell's current working directory:

```bash
./.venv/bin/archive-query \
	--question "What services were performed?" \
	--save-report
```

Override the output directory for one query with `--artifact-dir`:

```bash
./.venv/bin/archive-query \
	--question "What services were performed?" \
	--save-report \
	--artifact-dir /path/to/reports
```

The same default can be overridden for scripts with `ARTIFACT_OUTPUT_DIR`.
Reports contain retrieved evidence and the answer, so treat the output directory
as sensitive when processing personal, medical, tax, or financial records.
