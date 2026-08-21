import unittest

from archive_manager.domain.fact_validation import validate_event_facts


class FactValidationTest(unittest.TestCase):
    def test_valid_facts_are_marked_validated(self):
        report = validate_event_facts({"domain": "automotive_service", "vin": "2T1BURHE5EC081401", "total_charges": "158.16"})

        self.assertEqual(report.status, "validated")
        self.assertIn("vin", report.checked_fields)

    def test_invalid_facts_require_review(self):
        report = validate_event_facts({"domain": "automotive_service", "vin": "bad", "total_charges": "-1"})

        self.assertEqual(report.status, "needs_review")
        self.assertGreaterEqual(len(report.warnings), 2)

    def test_validation_is_serializable(self):
        report = validate_event_facts({"domain": "tax", "fields": [{"name": "tax_year"}]})

        self.assertEqual(report.to_dict()["status"], "validated")


if __name__ == "__main__":
    unittest.main()