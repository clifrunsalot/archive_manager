"""Document ingestion pipeline for the archive search system.

This module ingests source files from the archive input area, normalizes them into
PDFs, optionally runs OCR when the document is not already text-searchable,
extracts page text, chunks the content, and stores vector embeddings in Qdrant.

The workflow is designed to support both one-off ingestion from the command line
and background processing from the filesystem watcher.
"""

import argparse
import json
import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pdfplumber
import requests
import img2pdf

import archive_manager.config.settings_loader as settings_loader  # noqa: F401
from archive_manager.core.event_manifests import find_manifest_for_source, load_manifests
from archive_manager.core.event_facts import extract_event_facts, load_event_facts, save_event_facts
from archive_manager.ingestion.ocr_adapters import OCRRequest, get_ocr_backend
from archive_manager.lifecycle.trace_log import new_run_id, trace_event


class _PdfFontWarningFilter(logging.Filter):
    """Hide pdfminer warnings for malformed optional FontBBox metadata."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "Could not get FontBBox from font descriptor" not in record.getMessage()


logging.getLogger("pdfminer.pdffont").addFilter(_PdfFontWarningFilter())

from archive_manager.paths import PROJECT_ROOT

ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", PROJECT_ROOT / "ARCHIVE"))
SOURCE_DIR = Path(os.environ.get("SOURCE_DIR", PROJECT_ROOT / "data" / "source"))
SEARCHABLE_DIR = Path(os.environ.get("SEARCHABLE_DIR", PROJECT_ROOT / "data" / "searchable"))
LOGS_DIR = Path(os.environ.get("LOGS_DIR", PROJECT_ROOT / "logs"))
CACHE_DIR = Path(os.environ.get("CACHE_DIR", PROJECT_ROOT / "data" / ".ingest_cache"))
EVENT_MANIFEST_PATH = Path(
    os.environ.get("EVENT_MANIFEST_PATH", PROJECT_ROOT / "data" / "events.json")
)

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "archive_chunks")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text:latest")

OCR_ENGINE = os.environ.get("OCR_ENGINE", "paddleocr").lower()
PADDLEOCR_IMAGE = os.environ.get("PADDLEOCR_IMAGE", "archive-paddleocr:latest")

REQUEST_SESSION = requests.Session()
_QDRANT_COLLECTION_READY = False


def qdrant_request_headers() -> dict[str, str]:
    """Return authenticated request headers when Qdrant auth is configured."""
    api_key = os.environ.get("QDRANT_API_KEY", QDRANT_API_KEY)
    return {"api-key": api_key} if api_key else {}


def _get_ocr_backend_config():
    """Return the active OCR backend and image from the current environment."""
    engine = os.environ.get("OCR_ENGINE", OCR_ENGINE).lower()
    paddle_image = os.environ.get("PADDLEOCR_IMAGE", PADDLEOCR_IMAGE)
    return engine, paddle_image


def sha256_file_bytes(path: Path) -> str:
    """Return a stable SHA-256 digest for the bytes contained in a file.

    This value is used as a document identifier so repeated ingestion attempts can
    be detected and duplicate work can be skipped safely.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_ingest_cache():
    """Load the on-disk cache of already-ingested document hashes.

    Returns:
        dict: Mapping of document ID to the original source filename.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "ingested.json"
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_ingest_cache(cache):
    """Persist the ingest cache to disk as JSON.

    Args:
        cache: Dictionary keyed by document ID holding source filename values.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "ingested.json"
    with cache_path.open("w", encoding="utf-8") as fh:
        json.dump(cache, fh, sort_keys=True)


def load_indexed_sources():
    """Return unique filenames recorded after successful ingestion."""
    return sorted(set(load_ingest_cache().values()), key=str.casefold)


def ensure_pdf(input_path: Path, doc_id: str) -> Path:
    """Return a PDF path for the given source file.

    If the input is already a PDF, it is returned unchanged. If the input is an
    image, the image is converted to a single-page PDF and stored in the source
    directory under the document ID.

    Args:
        input_path: The original document or image file.
        doc_id: Stable document identifier used for the normalized PDF filename.

    Returns:
        Path: Location of the PDF version of the input.

    Raises:
        ValueError: If the input file type is unsupported or conversion yields no data.
    """
    input_path = input_path.resolve()
    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        return input_path

    if suffix not in {".png", ".jpg", ".jpeg"}:
        raise ValueError(f"Unsupported file type: {suffix}")

    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    out_pdf = SOURCE_DIR / f"{doc_id}.pdf"

    # Convert image -> one-page PDF
    with input_path.open("rb") as img_f:
        pdf_bytes = img2pdf.convert(img_f) or b""

    if not pdf_bytes:
        raise ValueError(f"Image conversion produced no PDF bytes for {input_path}")

    if not out_pdf.exists():
        out_pdf.write_bytes(pdf_bytes)

    return out_pdf


def ensure_source_pdf(pdf_path: Path, doc_id: str) -> Path:
    """Copy any normalized PDF to the stable path used by OCR containers."""
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    stable_pdf = SOURCE_DIR / f"{doc_id}.pdf"
    if not stable_pdf.exists():
        stable_pdf.write_bytes(pdf_path.read_bytes())
    return stable_pdf


def extract_pages_text_pdfplumber(pdf_path: Path):
    """Extract text content from each page of a PDF.

    Only pages with non-empty extracted text are returned. The result is a list of
    dictionaries with page numbers and the cleaned text from that page.
    """
    pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages):
            page_no = i + 1
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                pages.append({"page": page_no, "text": text})
    return pages


def is_pdf_searchable_min_chars(
    pdf_path: Path, sample_pages: int = 3, min_chars: int = 200
) -> bool:
    """Return True when the PDF appears to already contain readable text.

    The function reads a small number of pages and counts extracted characters.
    If the total exceeds the configured threshold, the PDF is treated as already
    searchable and OCR can be skipped.
    """
    with pdfplumber.open(str(pdf_path)) as pdf:
        max_i = min(len(pdf.pages), sample_pages)
        total = 0
        for i in range(max_i):
            text = (pdf.pages[i].extract_text() or "").strip()
            total += len(text)
            if total >= min_chars:
                return True
        return False


def is_image_likely_scanned(input_path: Path) -> bool:
    """Heuristic check for whether an image is likely a scanned document.

    This is used to decide whether a fast OCR pass is appropriate for image-based
    inputs. It inspects image dimensions and grayscale variance to identify
    page-like scanned content.
    """
    try:
        from PIL import Image
    except Exception:
        return False

    try:
        with Image.open(input_path) as img:
            img.load()
            width, height = img.size
            if width <= 0 or height <= 0:
                return False
            # Very small images are often not archive-quality scanned pages.
            if min(width, height) < 300:
                return False
            grayscale = img.convert("L")
            pixels = grayscale.resize((128, 128))
            data: list[int] = []
            for y in range(pixels.height):
                for x in range(pixels.width):
                    value = pixels.getpixel((x, y))
                    if isinstance(value, tuple):
                        value = value[0]
                    if value is None:
                        continue
                    data.append(int(float(value)))
            if not data:
                return False
            mean = sum(data) / len(data)
            variance = sum((p - mean) ** 2 for p in data) / len(data)
            return variance > 1000
    except Exception:
        return False


def chunk_text_by_chars(text: str, chunk_chars: int = 2500, overlap_chars: int = 300):
    """Split text into overlapping chunks of a fixed character size.

    Args:
        text: Source text to split.
        chunk_chars: Maximum length of each chunk.
        overlap_chars: Amount of overlap between adjacent chunks.

    Returns:
        list[str]: Chunk strings in order.
    """
    chunks = []
    start = 0
    n = len(text)
    if n == 0:
        return chunks
    while start < n:
        end = min(start + chunk_chars, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        start = end - overlap_chars
        if start < 0:
            start = 0
    return chunks


def ollama_embed_text(text: str):
    """Embed a single string using the configured Ollama embedding model."""
    return ollama_embed_texts([text])[0]


def ollama_embed_texts(texts):
    """Embed multiple strings and return the embedding vectors.

    If the newer batch embedding endpoint is unavailable, this function falls back
    to the legacy single-text endpoint for each item.
    """
    texts = [str(text) for text in texts]
    if not texts:
        return []

    try:
        r = REQUEST_SESSION.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": EMBED_MODEL, "input": texts, "keep_alive": 0},
            timeout=300,
        )
        r.raise_for_status()
        payload = r.json()
        if "embeddings" in payload:
            return payload["embeddings"]
        if "embedding" in payload:
            return [payload["embedding"]]
        if "data" in payload and isinstance(payload["data"], list):
            return [item["embedding"] for item in payload["data"]]
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code not in {404, 405}:
            raise

    embeddings = []
    for text in texts:
        embeddings.append(ollama_embed_text_v1(text))
    return embeddings


def ollama_embed_text_v1(text: str):
    """Embed a single string using the legacy Ollama embeddings endpoint."""
    r = REQUEST_SESSION.post(
        f"{OLLAMA_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text, "keep_alive": 0},
        timeout=300,
    )
    r.raise_for_status()
    data = r.json()
    return data["embedding"]


def ensure_qdrant_collection() -> None:
    """Create the configured Qdrant collection if it does not already exist."""
    global _QDRANT_COLLECTION_READY
    if _QDRANT_COLLECTION_READY:
        return

    collection_url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}"
    try:
        r = REQUEST_SESSION.get(collection_url, timeout=30, headers=qdrant_request_headers())
    except requests.RequestException:
        r = None

    if r is None or r.status_code != 200:
        create_payload = {
            "vectors": {
                "size": 768,
                "distance": "Cosine",
            }
        }
        r = REQUEST_SESSION.put(
            collection_url, json=create_payload, timeout=30, headers=qdrant_request_headers()
        )
        if not r.ok:
            print("Qdrant create status:", r.status_code)
            print("Qdrant create body:", r.text)
            r.raise_for_status()

    index_url = f"{collection_url}/index"
    for field_name in ("source", "event_id", "event_type", "subject_ref"):
        index_response = REQUEST_SESSION.put(
            index_url,
            json={"field_name": field_name, "field_schema": "keyword"},
            timeout=30,
            headers=qdrant_request_headers(),
        )
        if not index_response.ok and index_response.status_code != 409:
            index_response.raise_for_status()
    _QDRANT_COLLECTION_READY = True


def qdrant_upsert_points(points):
    """Insert or update one or more vectors into the Qdrant collection."""
    ensure_qdrant_collection()
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points?wait=true"
    r = REQUEST_SESSION.put(
        url, json={"points": points}, timeout=120, headers=qdrant_request_headers()
    )
    if not r.ok:
        print("Qdrant status:", r.status_code)
        print("Qdrant body:", r.text)
        r.raise_for_status()
    return r.json()


def log_stage_duration(log, stage_name: str, started_at: float):
    """Write a timing record for a pipeline stage to the ingest log."""
    elapsed = time.perf_counter() - started_at
    message = f"{stage_name}_seconds={elapsed:.2f}\n"
    log.write(message)
    print(f"[ingest] {message.strip()}")


def qdrant_search(query_embedding, top_k=10):
    """Query Qdrant for the nearest neighbors to a vector embedding."""
    ensure_qdrant_collection()
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search"
    payload = {
        "vector": query_embedding,
        "limit": top_k,
        "with_payload": True,
        "params": {
            "hnsw_ef": int(os.environ.get("QDRANT_HNSW_EF", "64")),
        },
    }
    r = REQUEST_SESSION.post(url, json=payload, timeout=30, headers=qdrant_request_headers())
    r.raise_for_status()
    return r.json()


def qdrant_search_by_source(source_name: str, limit: int = 100):
    """Return indexed chunks whose source filename exactly matches a name."""
    ensure_qdrant_collection()
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/scroll"
    payload = {
        "filter": {"must": [{"key": "source", "match": {"value": source_name}}]},
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    r = REQUEST_SESSION.post(url, json=payload, timeout=30, headers=qdrant_request_headers())
    r.raise_for_status()
    return r.json().get("result", {}).get("points", [])


def qdrant_search_by_source_embedding(query_embedding, source_name: str, limit: int = 5):
    """Return the most relevant chunks for one source filename."""
    ensure_qdrant_collection()
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/search"
    payload = {
        "vector": query_embedding,
        "filter": {"must": [{"key": "source", "match": {"value": source_name}}]},
        "limit": limit,
        "with_payload": True,
        "params": {"hnsw_ef": int(os.environ.get("QDRANT_HNSW_EF", "64"))},
    }
    r = REQUEST_SESSION.post(url, json=payload, timeout=30, headers=qdrant_request_headers())
    r.raise_for_status()
    return r.json().get("result", [])


def qdrant_search_by_source_regex(pattern: str, limit: int = 100):
    """Return indexed chunks whose source filename matches a regular expression."""
    if len(pattern) > 200:
        raise ValueError("Filename regex must be 200 characters or fewer")
    try:
        source_regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Invalid filename regex: {exc}") from exc

    ensure_qdrant_collection()
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/scroll"
    matches = []
    offset = None
    while len(matches) < limit:
        payload = {
            "limit": min(100, limit - len(matches)),
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            payload["offset"] = offset
        r = REQUEST_SESSION.post(url, json=payload, timeout=30, headers=qdrant_request_headers())
        r.raise_for_status()
        result = r.json().get("result", {})
        for point in result.get("points", []):
            source = str(point.get("payload", {}).get("source", ""))
            if source_regex.search(source):
                matches.append(point)
                if len(matches) >= limit:
                    break
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return matches


def qdrant_list_sources(limit: int = 10000):
    """Return unique source filenames currently represented in the index."""
    ensure_qdrant_collection()
    url = f"{QDRANT_URL}/collections/{QDRANT_COLLECTION}/points/scroll"
    sources = set()
    offset = None
    while len(sources) < limit:
        payload = {
            "limit": min(100, limit - len(sources)),
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            payload["offset"] = offset
        r = REQUEST_SESSION.post(url, json=payload, timeout=30, headers=qdrant_request_headers())
        r.raise_for_status()
        result = r.json().get("result", {})
        for point in result.get("points", []):
            source = str(point.get("payload", {}).get("source", "")).strip()
            if source:
                sources.add(source)
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return sorted(sources, key=str.casefold)


def run_ocr_docker(doc_id: str, fast_mode: bool = False):
    """Compatibility wrapper that runs the configured OCR backend.

    The normalized backend contract writes page-separated UTF-8 text to the
    searchable sidecar. ``fast_mode`` remains accepted for caller compatibility.
    """
    in_pdf = SOURCE_DIR / f"{doc_id}.pdf"
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    SEARCHABLE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _, paddle_image = _get_ocr_backend_config()
    request = OCRRequest(
        input_pdf=in_pdf,
        output_text=SEARCHABLE_DIR / f"{doc_id}.txt",
        output_json=SEARCHABLE_DIR / f"{doc_id}.ocr.json",
        source_dir=SOURCE_DIR,
        searchable_dir=SEARCHABLE_DIR,
        image=paddle_image,
        timeout_seconds=int(os.environ.get("OCR_TIMEOUT_SECONDS", "900")),
        render_max_side=int(os.environ.get("OCR_RENDER_MAX_SIDE", "3000")),
    )
    get_ocr_backend().run(request)


def ingest_pdf(input_path: Path, source_filename: str | None = None):
    """Ingest one source file into the archive search index.

    The function normalizes the input to PDF, optionally runs OCR, extracts text,
    chunks it, embeds each chunk with Ollama, and upserts the vectors into Qdrant.

    Args:
        input_path: A PDF or image file to ingest.
        source_filename: Optional override for the stored source filename.

    Returns:
        str: The document ID assigned to the ingested file.
    """
    start_time = time.perf_counter()
    run_id = new_run_id("ingest")
    input_path = input_path.resolve()
    source_filename = source_filename or input_path.name
    trace_event(run_id, "ingest", "BEGIN", source=source_filename, input_path=str(input_path))
    print(f"Processing input file: {source_filename}")

    event_manifest = find_manifest_for_source(
        load_manifests(EVENT_MANIFEST_PATH), source_filename
    )
    page_metadata = event_manifest.page_for(source_filename) if event_manifest else None

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    doc_id = sha256_file_bytes(input_path)
    ingest_cache = load_ingest_cache()
    if ingest_cache.get(doc_id) == input_path.name:
        print(f"[ingest] SKIPPED_ALREADY_INGESTED doc_id={doc_id} source={source_filename}")
        return doc_id

    # Normalize every input to the stable PDF path used by OCR and later stages.
    normalize_start = time.perf_counter()
    pdf_path = ensure_pdf(input_path, doc_id)
    pdf_path = ensure_source_pdf(pdf_path, doc_id)
    trace_event(run_id, "normalize_pdf", "END", status="completed", doc_id=doc_id, pdf_path=str(pdf_path))

    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    SEARCHABLE_DIR.mkdir(parents=True, exist_ok=True)

    log_path = LOGS_DIR / f"ingest_{doc_id}.log"
    with log_path.open("w") as log:
        log.write(f"doc_id={doc_id}\n")
        log.write(f"input={input_path}\n")
        log.write(f"pdf_path={pdf_path}\n")

        log_stage_duration(log, "normalize_pdf", normalize_start)

        searchable_start = time.perf_counter()
        searchable = is_pdf_searchable_min_chars(
            pdf_path,
            sample_pages=int(os.environ.get("SEARCHABLE_SAMPLE_PAGES", "3")),
            min_chars=int(os.environ.get("SEARCHABLE_MIN_CHARS", "200")),
        )
        log.write(f"is_searchable={searchable}\n")
        log_stage_duration(log, "pdf_searchability_check", searchable_start)

        if searchable:
            stable_searchable = SEARCHABLE_DIR / f"{doc_id}.pdf"
            if not stable_searchable.exists():
                stable_searchable.write_bytes(pdf_path.read_bytes())
                log.write(f"Copied searchable PDF to {stable_searchable}\n")
        else:
            fast_mode = os.environ.get("FAST_OCR", "1").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            should_fast_scan = False
            if input_path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                should_fast_scan = is_image_likely_scanned(input_path)

            if should_fast_scan or fast_mode:
                log.write("Running fast OCR container...\n")
                ocr_start = time.perf_counter()
                run_ocr_docker(doc_id, fast_mode=True)
                log_stage_duration(log, "ocr_fast", ocr_start)
                log.write("Fast OCR completed.\n")
                trace_event(run_id, "ocr", "END", status="completed", doc_id=doc_id, mode="fast")
            else:
                log.write("Running OCR container...\n")
                ocr_start = time.perf_counter()
                run_ocr_docker(doc_id)
                log_stage_duration(log, "ocr", ocr_start)
                log.write("OCR completed.\n")
                trace_event(run_id, "ocr", "END", status="completed", doc_id=doc_id, mode="standard")

        pdf_for_text = SEARCHABLE_DIR / f"{doc_id}.pdf"
        extraction_start = time.perf_counter()
        ocr_text_path = SEARCHABLE_DIR / f"{doc_id}.txt"
        if ocr_text_path.exists():
            page_text = ocr_text_path.read_text(encoding="utf-8")
            pages = [
                {"page": page_no, "text": text.strip()}
                for page_no, text in enumerate(page_text.split("\f"), start=1)
                if text.strip()
            ]
        else:
            pages = extract_pages_text_pdfplumber(pdf_for_text)
        log.write(f"Extracted pages with text: {len(pages)}\n")
        trace_event(run_id, "extract_text", "END", status="completed", doc_id=doc_id, page_count=len(pages), pages=pages)
        log_stage_duration(log, "extract_text", extraction_start)

        points = []
        chunk_chars = int(os.environ.get("CHUNK_CHARS", "2500"))
        overlap_chars = int(os.environ.get("CHUNK_OVERLAP_CHARS", "300"))
        embed_batch_size = int(os.environ.get("EMBED_BATCH_SIZE", "32"))
        embed_workers = max(1, int(os.environ.get("EMBED_WORKERS", "4")))

        doc_int_base = int(doc_id[:16], 16)  # stable numeric base
        pending = []
        pending_batches = []

        for p in pages:
            page_no = int(p["page"])
            page_text = p["text"]
            page_chunks = chunk_text_by_chars(
                page_text,
                chunk_chars=chunk_chars,
                overlap_chars=overlap_chars,
            )

            for idx, chunk_text in enumerate(page_chunks):
                if not chunk_text.strip():
                    continue

                point_id = doc_int_base + (page_no * 1_000_000) + idx
                pending.append(
                    {
                        "point_id": point_id,
                        "page": page_no,
                        "chunk_index": idx,
                        "chunk_text": chunk_text,
                    }
                )

                if len(pending) >= embed_batch_size:
                    pending_batches.append(list(pending))
                    pending.clear()

        if pending:
            pending_batches.append(list(pending))

        if pending_batches:
            embedding_start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=embed_workers) as executor:
                futures = {
                    executor.submit(
                        ollama_embed_texts,
                        [item["chunk_text"] for item in batch],
                    ): batch
                    for batch in pending_batches
                }

                for future in as_completed(futures):
                    batch = futures[future]
                    emb_list = future.result()
                    for item, emb in zip(batch, emb_list):
                        payload = {
                            "doc_id": doc_id,
                            "source": source_filename,
                            "page": item["page"],
                            "chunk_index": item["chunk_index"],
                            "text": item["chunk_text"],
                        }
                        if event_manifest:
                            payload.update(
                                {
                                    "event_id": event_manifest.event_id,
                                    "event_type": event_manifest.event_type,
                                }
                            )
                            if event_manifest.subject_ref:
                                payload["subject_ref"] = event_manifest.subject_ref
                            if page_metadata:
                                payload["event_page_number"] = page_metadata.page_number
                                payload["event_page_count"] = page_metadata.page_count
                        points.append(
                            {
                                "id": item["point_id"],
                                "vector": emb,
                                "payload": payload,
                            }
                        )

                    if len(points) >= 32:
                        qdrant_upsert_points(points)
                        points.clear()
            log_stage_duration(log, "embed_chunks", embedding_start)
            trace_event(run_id, "embed_chunks", "END", status="completed", doc_id=doc_id, chunk_count=sum(len(batch) for batch in pending_batches))

        if points:
            upsert_start = time.perf_counter()
            qdrant_upsert_points(points)
            log_stage_duration(log, "upsert_points", upsert_start)

        log.write("Upsert complete.\n")
        trace_event(run_id, "qdrant_upsert", "END", status="completed", doc_id=doc_id)

        if event_manifest:
            facts = extract_event_facts(event_manifest.event_id, event_manifest.event_type, pages)
            all_facts = load_event_facts()
            all_facts[event_manifest.event_id] = facts
            save_event_facts(all_facts)
            log.write(f"event_facts_saved={event_manifest.event_id}\n")
            trace_event(run_id, "event_facts", "END", status="completed", doc_id=doc_id, event_id=event_manifest.event_id, facts=facts)

    elapsed = time.perf_counter() - start_time
    completion_line = (
        f"PROCESS_COMPLETE doc_id={doc_id} source={source_filename} "
        f"elapsed_seconds={elapsed:.2f}\n"
    )
    with log_path.open("a") as log:
        log.write(completion_line)

    ingest_cache = load_ingest_cache()
    ingest_cache[doc_id] = input_path.name
    save_ingest_cache(ingest_cache)

    print(f"[ingest] PROCESS_COMPLETE doc_id={doc_id} source={source_filename} elapsed_seconds={elapsed:.2f}")
    trace_event(run_id, "ingest", "END", status="completed", doc_id=doc_id, source=source_filename, elapsed_seconds=round(elapsed, 2))

    return doc_id


def main():
    """CLI entry point for ingesting a single file from the command line."""
    parser = argparse.ArgumentParser(
        description="Ingest one PDF or image into the archive search index."
    )
    parser.add_argument("input_path_positional", type=Path, nargs="?", help="Path to a PDF, PNG, JPG, or JPEG file")
    parser.add_argument("--input", dest="input_path_named", type=Path, help="Path to a PDF, PNG, JPG, or JPEG file")
    args = parser.parse_args()

    if args.input_path_positional and args.input_path_named:
        parser.error("provide the input path either positionally or with --input, not both")
    input_path = args.input_path_named or args.input_path_positional
    if input_path is None:
        parser.error("an input path is required; use --input PATH")
    if not input_path.exists():
        raise SystemExit(f"File not found: {input_path}")

    doc_id = ingest_pdf(input_path)
    print(json.dumps({"doc_id": doc_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())