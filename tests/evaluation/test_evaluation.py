import tempfile
import unittest
from pathlib import Path

from archive_manager.evaluation.evaluation import EvaluationCase, evaluate_case, evaluate_file


class EvaluationTest(unittest.TestCase):
    def test_evaluate_case_checks_intent_and_answer(self):
        case = EvaluationCase("total charges on Sep 12 2024", "total_charges", ["$158.16"])
        result = evaluate_case(case, lambda question: "Total charges for 2024-09-12: $158.16")

        self.assertTrue(result["intent_pass"])
        self.assertTrue(result["answer_pass"])

    def test_evaluate_file_returns_aggregate_metrics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cases.json"
            path.write_text(
                '[{"question": "list files", "expected_intent": "source_inventory", "expected_substrings": ["file"]}]',
                encoding="utf-8",
            )
            result = evaluate_file(path, lambda question: "file")

        self.assertEqual(result["case_count"], 1)
        self.assertEqual(result["answer_pass_count"], 1)


if __name__ == "__main__":
    unittest.main()
