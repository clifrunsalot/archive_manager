import unittest

from ocr.paddleocr_runner import _quality_report


class PaddleOCRQualityReportTest(unittest.TestCase):
    def test_quality_report_is_domain_neutral(self):
        report = _quality_report([
            {
                "page": 1,
                "elements": [
                    {"text": "Invoice", "confidence": 0.9},
                    {"text": "Total", "confidence": 0.6},
                ],
            },
            {"page": 2, "elements": []},
        ])

        self.assertEqual(report["page_count"], 2)
        self.assertEqual(report["empty_pages"], 1)
        self.assertEqual(report["low_confidence_elements"], 1)
        self.assertEqual(report["mean_confidence"], 0.75)
        self.assertEqual(report["pages"][1]["mean_confidence"], None)


if __name__ == "__main__":
    unittest.main()

import unittest

from ocr.paddleocr_runner import _result_elements


class PaddleOCRRunnerTest(unittest.TestCase):
    def test_normalizes_text_score_and_box(self):
        result = {
            "rec_texts": ["TOTAL CHARGES", "158.16"],
            "rec_scores": [0.98, 0.96],
            "rec_boxes": [[1, 2, 3, 4], [5, 6, 7, 8]],
        }

        self.assertEqual(
            _result_elements(result),
            [
                {"text": "TOTAL CHARGES", "confidence": 0.98, "bbox": [1, 2, 3, 4]},
                {"text": "158.16", "confidence": 0.96, "bbox": [5, 6, 7, 8]},
            ],
        )


if __name__ == "__main__":
    unittest.main()