"""OCR backend boundary used by the ingestion pipeline.

Each backend writes the same page-separated UTF-8 sidecar contract. New engines
can be added here without changing document normalization, chunking, embedding,
or Qdrant code.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

import requests


SUPPORTED_OCR_ENGINES = {"paddleocr"}
DEFAULT_PADDLEOCR_SERVICE_URL = "http://localhost:8000"


@dataclass(frozen=True)
class OCRRequest:
    """Inputs shared by all OCR backends."""

    input_pdf: Path
    output_text: Path
    source_dir: Path
    searchable_dir: Path
    output_json: Path | None = None
    model_volume: str = "paddleocr_models"
    image: str = "archive-paddleocr:latest"
    timeout_seconds: int = 900
    render_max_side: int = 3000


class OCRBackend:
    """Interface for a backend that produces page-separated OCR text."""

    name = "base"

    def build_command(self, request: OCRRequest) -> list[str]:
        raise NotImplementedError

    def run(self, request: OCRRequest):
        request.output_text.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            self.build_command(request),
            check=True,
            timeout=request.timeout_seconds,
        )


class PaddleOCRBackend(OCRBackend):
    """Run PaddleOCR, preferring the persistent service and falling back to a one-shot container."""

    name = "paddleocr"

    def build_command(self, request: OCRRequest) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{request.source_dir}:/work/source",
            "-v",
            f"{request.searchable_dir}:/work/searchable",
            "-v",
            f"{request.model_volume}:/root/.paddlex",
            "-e",
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True",
            "-e",
            f"OCR_RENDER_MAX_SIDE={request.render_max_side}",
            request.image,
            "python",
            "/work/paddleocr_runner.py",
            "--input",
            f"/work/source/{request.input_pdf.name}",
            "--output",
            f"/work/searchable/{request.output_text.name}",
        ] + (["--output-json", f"/work/searchable/{request.output_json.name}"] if request.output_json else [])

    def run(self, request: OCRRequest):
        request.output_text.parent.mkdir(parents=True, exist_ok=True)
        if self._run_via_service(request):
            return
        subprocess.run(
            self.build_command(request),
            check=True,
            timeout=request.timeout_seconds,
        )

    def _run_via_service(self, request: OCRRequest) -> bool:
        """Try the persistent OCR service (keeps the model loaded) before a one-shot container.

        Returns True on success, False if the service is unreachable or rejects the
        request, in which case the caller should fall back to ``docker run --rm``.
        """
        service_url = os.environ.get("PADDLEOCR_SERVICE_URL", DEFAULT_PADDLEOCR_SERVICE_URL)
        payload = {
            "input": request.input_pdf.name,
            "output": request.output_text.name,
            "render_max_side": request.render_max_side,
        }
        if request.output_json:
            payload["output_json"] = request.output_json.name
        try:
            response = requests.post(f"{service_url}/ocr", json=payload, timeout=request.timeout_seconds)
        except requests.exceptions.RequestException:
            return False
        return response.status_code == 200


BACKENDS: dict[str, OCRBackend] = {"paddleocr": PaddleOCRBackend()}


def get_ocr_backend(name: str | None = None) -> OCRBackend:
    """Return a configured OCR backend or raise a clear configuration error."""
    engine = (name or os.environ.get("OCR_ENGINE", "paddleocr")).lower()
    try:
        return BACKENDS[engine]
    except KeyError as exc:
        supported = ", ".join(sorted(BACKENDS))
        raise ValueError(f"Unsupported OCR engine '{engine}'. Supported engines: {supported}") from exc