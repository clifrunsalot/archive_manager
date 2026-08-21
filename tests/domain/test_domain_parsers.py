import unittest

from archive_manager.domain.domain_parsers import get_parser


class DomainParserTest(unittest.TestCase):
    def test_banking_parser_extracts_labeled_period(self):
        result = get_parser("banking_statement").parse(
            ["Statement Period: Jan 01 - Jan 31, 2026\nAccount Type: Checking"]
        )

        self.assertEqual(result.field("statement_period").value, "Jan 01 - Jan 31, 2026")
        self.assertEqual(result.field("statement_period").page, 1)
        self.assertEqual(result.field("account_type").value, "Checking")

    def test_insurance_parser_does_not_infer_missing_fields(self):
        result = get_parser("insurance").parse(["Coverage information follows."])

        self.assertEqual(result.fields, [])

    def test_tax_parser_extracts_form_and_year(self):
        result = get_parser("tax").parse(["Form 1040\nTax Year: 2025"])

        self.assertEqual(result.field("form_type").value, "Form 1040")
        self.assertEqual(result.field("tax_year").value, "2025")

    def test_unknown_domain_uses_safe_fallback(self):
        result = get_parser("unknown").parse(["Account balance: $1000"])

        self.assertEqual(result.domain, "general_document")
        self.assertEqual(result.fields, [])

    def test_medical_parser_extracts_provider_and_visit_date(self):
        result = get_parser("medical").parse(
            ["Provider: Taylor Morgan\nVisit Date: 08/19/2026"]
        )

        self.assertEqual(result.field("provider").value, "Taylor Morgan")
        self.assertEqual(result.field("visit_date").value, "08/19/2026")

    def test_financial_parsers_extract_labeled_amounts_without_inference(self):
        banking = get_parser("banking_statement").parse(["Ending Balance: $1,234.56"])
        tax = get_parser("tax").parse(["AGI: $80,000.00"])

        self.assertEqual(banking.field("ending_balance").value, "1,234.56")
        self.assertEqual(tax.field("total_income").value, "80,000.00")


if __name__ == "__main__":
    unittest.main()