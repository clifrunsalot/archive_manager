import tempfile
import unittest
from pathlib import Path

from archive_manager.retrieval.hybrid_retrieval import lexical_search


class HybridRetrievalTest(unittest.TestCase):
    def test_exact_tokens_rank_matching_sidecar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "one.txt").write_text("VIN 2T1BURHE5EC081401 total charges", encoding="utf-8")
            (root / "two.txt").write_text("unrelated document", encoding="utf-8")
            hits = lexical_search(
                "What are the total charges for VIN 2T1BURHE5EC081401?",
                {"one": "one.png", "two": "two.png"},
                root,
            )

        self.assertEqual(hits[0]["payload"]["source"], "one.png")


if __name__ == "__main__":
    unittest.main()