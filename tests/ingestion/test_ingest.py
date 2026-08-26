import tempfile
import unittest
import logging
from pathlib import Path
from unittest.mock import patch

import archive_manager.ingestion.ingest as ingest


class IngestPdfEfficiencyTest(unittest.TestCase):
    def test_pdf_font_warning_filter_suppresses_only_fontbbox_warning(self):
        record = logging.LogRecord(
            "pdfminer.pdffont",
            logging.WARNING,
            __file__,
            1,
            "Could not get FontBBox from font descriptor because None cannot be parsed as 4 floats",
            (),
            None,
        )

        self.assertFalse(ingest._PdfFontWarningFilter().filter(record))

    def test_embedding_requests_unload_model_after_use(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"embeddings": [[0.1, 0.2]]}

        with patch.object(ingest.REQUEST_SESSION, "post", return_value=Response()) as post:
            self.assertEqual(ingest.ollama_embed_texts(["text"]), [[0.1, 0.2]])

        self.assertEqual(post.call_args.kwargs["json"]["keep_alive"], 0)

    def test_pdf_input_is_copied_to_stable_source_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_pdf = root / "input.pdf"
            source_dir = root / "source"
            input_pdf.write_bytes(b"%PDF-1.4\ncontent")

            with patch.object(ingest, "SOURCE_DIR", source_dir):
                stable_pdf = ingest.ensure_source_pdf(input_pdf, "abc123")

            self.assertEqual(stable_pdf, source_dir / "abc123.pdf")
            self.assertEqual(stable_pdf.read_bytes(), input_pdf.read_bytes())

    def test_ingest_skips_ocr_for_searchable_pdf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "already_searchable.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")

            seen = {}

            original_sha = ingest.sha256_file_bytes
            original_ensure = ingest.ensure_pdf
            original_searchable = ingest.is_pdf_searchable_min_chars
            original_extract = ingest.extract_pages_text_pdfplumber
            original_embed = ingest.ollama_embed_texts
            original_ocr = ingest.run_ocr_docker
            original_upsert = ingest.qdrant_upsert_points
            original_cache_loader = ingest.load_ingest_cache
            original_cache_saver = ingest.save_ingest_cache

            try:
                ingest.sha256_file_bytes = lambda path: "a" * 64
                ingest.load_ingest_cache = lambda: {}
                ingest.save_ingest_cache = lambda cache: None
                ingest.ensure_pdf = lambda input_path, doc_id: pdf_path
                ingest.is_pdf_searchable_min_chars = lambda pdf_path, sample_pages=3, min_chars=200: True
                ingest.extract_pages_text_pdfplumber = lambda pdf_path: [{"page": 1, "text": "A" * 300}]
                ingest.ollama_embed_texts = lambda texts: [[0.1, 0.2] for _ in texts]

                def fail_ocr(doc_id):
                    raise AssertionError("OCR should not run for searchable PDFs")

                ingest.run_ocr_docker = fail_ocr

                def fake_upsert(points):
                    seen["points"] = points
                    return {"result": []}

                ingest.qdrant_upsert_points = fake_upsert

                doc_id = ingest.ingest_pdf(pdf_path, source_filename="already_searchable.pdf")

                self.assertEqual(doc_id, "a" * 64)
                self.assertTrue(seen["points"])
                self.assertEqual(seen["points"][0]["payload"]["source"], "already_searchable.pdf")
            finally:
                ingest.sha256_file_bytes = original_sha
                ingest.load_ingest_cache = original_cache_loader
                ingest.save_ingest_cache = original_cache_saver
                ingest.ensure_pdf = original_ensure
                ingest.is_pdf_searchable_min_chars = original_searchable
                ingest.extract_pages_text_pdfplumber = original_extract
                ingest.ollama_embed_texts = original_embed
                ingest.run_ocr_docker = original_ocr
                ingest.qdrant_upsert_points = original_upsert

    def test_run_ocr_docker_uses_paddleocr_when_configured(self):
        captured = {}

        original_run = ingest.subprocess.run
        try:
            with patch.dict("os.environ", {"OCR_ENGINE": "paddleocr", "PADDLEOCR_IMAGE": "paddlepaddle/paddleocr:latest-cpu"}, clear=False):
                def fake_run(cmd, check, timeout):
                    captured["cmd"] = cmd
                    captured["timeout"] = timeout
                    return None

                ingest.subprocess.run = fake_run
                ingest.run_ocr_docker("abc123")
        finally:
            ingest.subprocess.run = original_run

        self.assertIn("paddlepaddle/paddleocr:latest-cpu", captured["cmd"])
        self.assertIn("/work/paddleocr_runner.py", captured["cmd"])
        self.assertIn("--input", captured["cmd"])
        self.assertEqual(captured["timeout"], 900)

    def test_qdrant_upsert_creates_collection_if_missing(self):
        calls = []

        class FakeResponse:
            def __init__(self, status_code=200, payload=None):
                self.status_code = status_code
                self.ok = status_code < 400
                self._payload = payload or {}
                self.text = ""

            def json(self):
                return self._payload

            def raise_for_status(self):
                if not self.ok:
                    raise RuntimeError("HTTP error")

        def fake_get(url, timeout, headers=None):
            calls.append(("get", url))
            return FakeResponse(404)

        def fake_put(url, json=None, timeout=None, headers=None):
            calls.append(("put", url, json))
            if url.endswith("/collections/archive_chunks"):
                return FakeResponse(200, {"result": "created"})
            return FakeResponse(200, {"result": "updated"})

        with patch.object(ingest.REQUEST_SESSION, "get", side_effect=fake_get), patch.object(ingest.REQUEST_SESSION, "put", side_effect=fake_put):
            ingest.qdrant_upsert_points([
                {"id": 1, "vector": [0.1, 0.2], "payload": {"text": "hello"}}
            ])

        self.assertEqual(calls[0][0], "get")
        self.assertIn("/collections/archive_chunks", calls[0][1])
        self.assertEqual(calls[1][0], "put")
        self.assertIn("/collections/archive_chunks", calls[1][1])
        index_calls = [call for call in calls if "/collections/archive_chunks/index" in call[1]]
        self.assertEqual(len(index_calls), 4)
        self.assertEqual(
            {call[2]["field_name"] for call in index_calls},
            {"source", "event_id", "event_type", "subject_ref"},
        )
        point_calls = [call for call in calls if "/points?wait=true" in call[1]]
        self.assertEqual(len(point_calls), 1)

    def test_qdrant_api_key_is_sent_as_header(self):
        with patch.dict("os.environ", {"QDRANT_API_KEY": "test-key"}, clear=False):
            self.assertEqual(ingest.qdrant_request_headers(), {"api-key": "test-key"})

    def test_sensitive_mode_omits_subject_reference_from_payload(self):
        class Manifest:
            event_id = "event-1"
            event_type = "general_document"
            subject_ref = "person-1"

        with patch.dict("os.environ", {"ARCHIVE_SECURITY_MODE": "sensitive"}, clear=False):
            payload = ingest._event_payload_metadata(Manifest(), None)

        self.assertEqual(payload, {"event_id": "event-1", "event_type": "general_document"})

        with patch.dict("os.environ", {"ARCHIVE_SECURITY_MODE": ""}, clear=False):
            payload = ingest._event_payload_metadata(Manifest(), None)

        self.assertEqual(payload["subject_ref"], "person-1")


if __name__ == "__main__":
    unittest.main()
