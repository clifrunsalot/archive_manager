import os
import unittest
from pathlib import Path
from unittest.mock import patch

import archive_manager.ingestion.ingest as ingest
import archive_manager.retrieval.query as query
from archive_manager.core.encryption import generate_key
from archive_manager.security.security_config import validate_sensitive_configuration


class SensitiveConfigurationTest(unittest.TestCase):
    def test_compatibility_mode_does_not_require_sensitive_settings(self):
        with patch.dict(os.environ, {"ARCHIVE_SECURITY_MODE": ""}, clear=False):
            validate_sensitive_configuration()

    def test_sensitive_mode_requires_encryption_and_qdrant_keys(self):
        with patch.dict(os.environ, {"ARCHIVE_SECURITY_MODE": "sensitive"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ARCHIVE_ENCRYPTION_KEY"):
                validate_sensitive_configuration()

    def test_sensitive_mode_requires_local_ollama(self):
        with patch.dict(
            os.environ,
            {
                "ARCHIVE_SECURITY_MODE": "sensitive",
                "ARCHIVE_ENCRYPTION_KEY": generate_key(),
                "QDRANT_API_KEY": "local-secret",
                "OLLAMA_BASE": "https://example.invalid",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "OLLAMA_BASE"):
                validate_sensitive_configuration()

    def test_sensitive_mode_blocks_ingestion_before_file_access(self):
        with patch.dict(os.environ, {"ARCHIVE_SECURITY_MODE": "sensitive"}, clear=True), patch.object(
            ingest, "sha256_file_bytes", side_effect=AssertionError("file access should not occur")
        ):
            with self.assertRaisesRegex(RuntimeError, "ARCHIVE_ENCRYPTION_KEY"):
                ingest.ingest_pdf(Path("/does/not/exist.pdf"))

    def test_sensitive_mode_blocks_query_before_retrieval(self):
        with patch.dict(os.environ, {"ARCHIVE_SECURITY_MODE": "sensitive"}, clear=True), patch.object(
            query, "ollama_embed_text", side_effect=AssertionError("retrieval should not occur")
        ):
            with self.assertRaisesRegex(RuntimeError, "ARCHIVE_ENCRYPTION_KEY"):
                query.answer("private query")


if __name__ == "__main__":
    unittest.main()