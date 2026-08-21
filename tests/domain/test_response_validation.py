import unittest

from archive_manager.domain.response_validation import validate_summary


class ResponseValidationTest(unittest.TestCase):
    def test_valid_summary(self):
        result = validate_summary(
            "Service date 2024-09-12. Total charges $158.16.",
            {"service_date": "2024-09-12", "total_charges": "158.16"},
        )

        self.assertEqual(result["status"], "valid")

    def test_contradictory_summary_is_invalid(self):
        result = validate_summary(
            "Total charges $999.00. Recommended but not performed work included.",
            {"service_date": "2024-09-12", "total_charges": "158.16"},
        )

        self.assertEqual(result["status"], "invalid")
        self.assertGreaterEqual(len(result["warnings"]), 2)


if __name__ == "__main__":
    unittest.main()