import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archive_manager.config.settings_loader import load_settings


class SettingsLoaderTest(unittest.TestCase):
    def test_loads_defaults_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.env"
            path.write_text(
                "OLLAMA_TOP_P=0.2\nexport OLLAMA_TOP_K=20\nOLLAMA_NUM_CTX=16384\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"OLLAMA_TOP_K": "7"}, clear=True):
                load_settings(path)

                self.assertEqual(os.environ["OLLAMA_TOP_P"], "0.2")
                self.assertEqual(os.environ["OLLAMA_TOP_K"], "7")
                self.assertEqual(os.environ["OLLAMA_NUM_CTX"], "16384")


if __name__ == "__main__":
    unittest.main()