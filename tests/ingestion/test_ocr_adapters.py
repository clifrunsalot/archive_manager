import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from archive_manager.ingestion.ocr_adapters import OCRRequest, get_ocr_backend


class OCRAdapterTest(unittest.TestCase):
    def test_paddle_backend_preserves_runner_contract(self):
        request = OCRRequest(
            input_pdf=Path("/tmp/source/abc.pdf"),
            output_text=Path("/tmp/searchable/abc.txt"),
            output_json=Path("/tmp/searchable/abc.ocr.json"),
            source_dir=Path("/tmp/source"),
            searchable_dir=Path("/tmp/searchable"),
            image="test-paddle:latest",
        )

        command = get_ocr_backend("paddleocr").build_command(request)

        self.assertIn("test-paddle:latest", command)
        self.assertIn("/work/source/abc.pdf", command)
        self.assertIn("/work/searchable/abc.txt", command)
        self.assertIn("--output-json", command)
        self.assertIn("/work/searchable/abc.ocr.json", command)

    def test_unknown_backend_fails_with_supported_names(self):
        with self.assertRaisesRegex(ValueError, "Supported engines: paddleocr"):
            get_ocr_backend("docling")

    def test_run_prefers_persistent_service_and_skips_container(self):
        request = OCRRequest(
            input_pdf=Path("/tmp/source/abc.pdf"),
            output_text=Path("/tmp/searchable/abc.txt"),
            source_dir=Path("/tmp/source"),
            searchable_dir=Path("/tmp/searchable"),
        )
        backend = get_ocr_backend("paddleocr")

        with patch("archive_manager.ingestion.ocr_adapters.requests.post") as mock_post, \
                patch("archive_manager.ingestion.ocr_adapters.subprocess.run") as mock_subprocess_run:
            mock_post.return_value = Mock(status_code=200)
            backend.run(request)

        mock_post.assert_called_once()
        mock_subprocess_run.assert_not_called()

    def test_run_falls_back_to_container_when_service_unreachable(self):
        request = OCRRequest(
            input_pdf=Path("/tmp/source/abc.pdf"),
            output_text=Path("/tmp/searchable/abc.txt"),
            source_dir=Path("/tmp/source"),
            searchable_dir=Path("/tmp/searchable"),
        )
        backend = get_ocr_backend("paddleocr")

        with patch(
            "archive_manager.ingestion.ocr_adapters.requests.post",
            side_effect=requests.exceptions.ConnectionError,
        ), patch("archive_manager.ingestion.ocr_adapters.subprocess.run") as mock_subprocess_run:
            backend.run(request)

        mock_subprocess_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()