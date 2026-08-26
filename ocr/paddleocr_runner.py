"""Run PaddleOCR on a PDF and write extracted text for archive ingestion.

Supports two modes: a one-shot CLI invocation (``--input``/``--output``) for
backward compatibility, and a persistent HTTP service (``--serve``) that loads
the PaddleOCR model once and reuses it across requests, avoiding the model
initialization cost on every document.
"""

import argparse
import contextlib
import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path


def _result_text(result) -> str:
    """Extract recognized lines from one PaddleOCR result object."""
    data = result.json if hasattr(result, "json") else result
    if callable(data):
        data = data()
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict) and "res" in data:
        data = data["res"]
    if not isinstance(data, dict):
        return ""
    texts = data.get("rec_texts", [])
    return "\n".join(str(text).strip() for text in texts if str(text).strip())


def _result_elements(result) -> list[dict]:
    """Normalize PaddleOCR text, confidence, and coordinates when available."""
    data = result.json if hasattr(result, "json") else result
    if callable(data):
        data = data()
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict) and "res" in data:
        data = data["res"]
    if not isinstance(data, dict):
        return []
    texts = data.get("rec_texts", [])
    scores = data.get("rec_scores", [])
    boxes = data.get("rec_boxes", data.get("rec_polys", []))
    elements = []
    for index, text in enumerate(texts):
        value = str(text).strip()
        if not value:
            continue
        score = scores[index] if index < len(scores) else None
        box = boxes[index] if index < len(boxes) else None
        elements.append({
            "text": value,
            "confidence": float(score) if score is not None else None,
            "bbox": box.tolist() if hasattr(box, "tolist") else box,
        })
    return elements


def _quality_report(pages: list[dict]) -> dict:
    """Summarize OCR coverage and confidence without assuming a document domain."""
    page_reports = []
    all_confidences = []
    for page in pages:
        elements = page.get("elements", [])
        confidences = [
            element["confidence"]
            for element in elements
            if element.get("confidence") is not None
        ]
        all_confidences.extend(confidences)
        page_reports.append({
            "page": page.get("page"),
            "text_elements": len(elements),
            "text_characters": sum(len(element.get("text", "")) for element in elements),
            "mean_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
            "low_confidence_elements": sum(confidence < 0.75 for confidence in confidences),
            "empty": not bool(elements),
        })
    return {
        "page_count": len(pages),
        "empty_pages": sum(report["empty"] for report in page_reports),
        "mean_confidence": round(sum(all_confidences) / len(all_confidences), 4) if all_confidences else None,
        "low_confidence_elements": sum(report["low_confidence_elements"] for report in page_reports),
        "pages": page_reports,
    }


_OCR_LOCK = threading.Lock()
_OCR_INSTANCE = None


def _get_ocr_instance():
    """Load the PaddleOCR pipeline once and reuse it across calls."""
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        paddle_output = StringIO()
        try:
            with contextlib.redirect_stdout(paddle_output), contextlib.redirect_stderr(paddle_output):
                from paddleocr import PaddleOCR

                _OCR_INSTANCE = PaddleOCR(
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
        except Exception:
            print(paddle_output.getvalue(), end="", flush=True)
            raise
    return _OCR_INSTANCE


def run_ocr_job(input_pdf: Path, output_text: Path, output_json: Path | None, render_max_side: int) -> None:
    """Render a PDF's pages and OCR them, writing the page-separated sidecar contract."""
    ocr = _get_ocr_instance()
    paddle_output = StringIO()
    with tempfile.TemporaryDirectory() as temp_dir:
        page_prefix = Path(temp_dir) / "page"
        print("[paddleocr] Rendering PDF pages", flush=True)
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-scale-to",
                str(render_max_side),
                str(input_pdf),
                str(page_prefix),
            ],
            check=True,
        )
        page_paths = sorted(Path(temp_dir).glob("page-*.png"))
        print(f"[paddleocr] Rendered {len(page_paths)} page(s)", flush=True)
        page_text = []
        structured_pages = []
        for page_number, page_path in enumerate(page_paths, start=1):
            print(f"[paddleocr] Processing page {page_number}/{len(page_paths)}", flush=True)
            try:
                with contextlib.redirect_stdout(paddle_output), contextlib.redirect_stderr(paddle_output):
                    page_results = ocr.predict(str(page_path))
            except Exception:
                print(paddle_output.getvalue(), end="", flush=True)
                raise
            page_text.extend(_result_text(result) for result in page_results)
            elements = [element for result in page_results for element in _result_elements(result)]
            structured_pages.append({"page": page_number, "elements": elements})
    output_text.parent.mkdir(parents=True, exist_ok=True)
    output_text.write_text("\f".join(page_text), encoding="utf-8")
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps(
                {
                    "engine": "paddleocr",
                    "pages": structured_pages,
                    "quality": _quality_report(structured_pages),
                },
                indent=2,
            ),
            encoding="utf-8",
        )


class _OCRRequestHandler(BaseHTTPRequestHandler):
    """Minimal HTTP endpoint that keeps the OCR model resident between calls."""

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/ocr":
            self._send_json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            input_pdf = Path("/work/source") / payload["input"]
            output_text = Path("/work/searchable") / payload["output"]
            output_json = (
                Path("/work/searchable") / payload["output_json"] if payload.get("output_json") else None
            )
            render_max_side = int(payload.get("render_max_side", os.environ.get("OCR_RENDER_MAX_SIDE", "3000")))
            # Serialize requests: the PaddleOCR pipeline instance is not thread-safe.
            with _OCR_LOCK:
                run_ocr_job(input_pdf, output_text, output_json, render_max_side)
            self._send_json(200, {"status": "ok"})
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def log_message(self, log_format, *args):
        print(f"[paddleocr-service] {self.address_string()} - {log_format % args}", flush=True)


def serve(host: str, port: int) -> None:
    """Run the persistent OCR HTTP service, loading the model once at startup."""
    print("[paddleocr] Loading model...", flush=True)
    _get_ocr_instance()
    print(f"[paddleocr] Serving on {host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), _OCRRequestHandler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--serve", action="store_true", help="Run as a persistent HTTP service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.serve:
        serve(args.host, args.port)
        return

    if not args.input or not args.output:
        parser.error("--input and --output are required unless --serve is set")

    render_max_side = int(os.environ.get("OCR_RENDER_MAX_SIDE", "3000"))
    run_ocr_job(args.input, args.output, args.output_json, render_max_side)


if __name__ == "__main__":
    main()
