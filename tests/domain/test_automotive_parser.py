import unittest

from archive_manager.domain.automotive_parser import extract_automotive_facts, extract_labeled_values, extract_not_performed_services, extract_performed_services, extract_service_advisor, extract_service_causes


class AutomotiveParserTest(unittest.TestCase):
    def test_extracts_facts_across_multiple_pages(self):
        facts = extract_automotive_facts(
            [
                "VIN: 2T1BURHE5EC081401\n24NOV25\nRear Drum Brake",
                "Oil and Filter Change\nTOTAL CHARGES\n198.65",
            ]
        )

        self.assertEqual(facts["vin"], "2T1BURHE5EC081401")
        self.assertEqual(facts["service_date"], "24NOV25")
        self.assertEqual(facts["total_charges"], "198.65")

    def test_supports_alternate_total_labels_and_currency_formatting(self):
        facts = extract_automotive_facts(["TOTAL COSTS: $1,234.56"])

        self.assertEqual(facts["total_charges"], "1234.56")

    def test_does_not_truncate_four_digit_totals_without_thousands_comma(self):
        facts = extract_automotive_facts(["TOTAL CHARGES\n1138.29"])

        self.assertEqual(facts["total_charges"], "1138.29")

    def test_prefers_total_charges_over_please_pay(self):
        facts = extract_automotive_facts(
            ["TOTAL CHARGES\n158.16\nPLEASE PAY\n168.64"]
        )

        self.assertEqual(facts["total_charges"], "158.16")

    def test_reports_conflicting_identifiers(self):
        facts = extract_automotive_facts(
            ["VIN: 2T1BURHE5EC081401\nVIN: 1HGCM82633A004352\n12SEP24"]
        )

        self.assertIsNone(facts["vin"])
        self.assertIn("conflicting VINs", facts["warnings"])

    def test_excludes_quotes_and_recommended_work(self):
        services = extract_performed_services([
            "A PRICE ON LEFT FRONT WHEEL COVER\n"
            "B PERFORM MULTI POINT INSPECTION\n"
            "C Tire Rotation and Synthetic Oil Change Special $99.95\n"
            "D REPLACE ENGINE AIR FILTER\n"
            "THE FOLLOWING WORK WAS RECOMMENDED BUT NOT PERFORMED\n"
            "BR21 Brake Drums Or Rotors (Rear) - Replace (Both)"
        ])

        self.assertEqual(
            services,
            [
                "Perform Multi Point Inspection",
                "Tire Rotation And Synthetic Oil Change Special",
                "Replace Engine Air Filter",
            ],
        )

    def test_deduplicates_oil_change_inside_tire_oil_package(self):
        services = extract_performed_services([
            "Tire Rotation and Synthetic Oil Change Special\n"
            "CAUSE: Completed lube oil and filter change with tire rotation\n"
            "Oil and Filter Change"
        ])

        self.assertEqual(services, ["Tire Rotation And Synthetic Oil Change Special"])

    def test_combined_pdf_pages_keep_later_performed_services(self):
        services = extract_performed_services([
            "RECOMMENDED BUT NOT PERFORMED\nReplace Rear Brakes\f"
            "PERFORM MULTI POINT INSPECTION\n"
            "Tire Rotation and Synthetic Oil Change Special\n"
            "REPLACE ENGINE AIR FILTER"
        ])

        self.assertIn("Perform Multi Point Inspection", services)
        self.assertIn("Replace Engine Air Filter", services)
        self.assertNotIn("Replace Rear Brakes", services)

    def test_extracts_recommended_services(self):
        services = extract_not_performed_services([
            "RECOMMENDED BUT NOT PERFORMED\n"
            "MA83 Cabin Air Filter (Standard)\n"
            "FS296 Stabilizer Link, Front\n"
            "BR21 Brake Drums Or Rotors (Rear) - Replace (Both)\n"
            "DESCRIPTION TOTALS"
        ])

        self.assertTrue(any("Cabin Air Filter" in service for service in services))
        self.assertTrue(any("Stabilizer Link" in service for service in services))

    def test_not_performed_extraction_stops_before_invoice_footer(self):
        services = extract_not_performed_services([
            "RECOMMENDED BUT NOT PERFORMED\n"
            "MA50 Windshield Wiper Blades (OEM) - Replace\n"
            "DESCRIPTION\nTOTALS\nLABOR AMOUNT\n302.34\nTOTAL CHARGES\n414.03"
        ])

        self.assertEqual(services, ["Windshield Wiper Blades (OEM) - Replace"])

    def test_extracts_service_advisor(self):
        self.assertEqual(
            extract_service_advisor(["SERVICE ADVISOR: 140O COLEEN ROBINSON"]),
            "1400 COLEEN ROBINSON",
        )

    def test_extracts_labeled_repair_causes(self):
        causes = extract_service_causes([
            "PADS AND ROTORS, FRONT - REPLACE\nCAUSE: WORN/RUSTED\n"
            "PERFORM BRAKE SERVICE\nCAUSE: MAINTENANCE"
        ])

        self.assertEqual(causes, [
            ("PADS AND ROTORS, FRONT - REPLACE", "WORN/RUSTED"),
            ("PERFORM BRAKE SERVICE", "MAINTENANCE"),
        ])

    def test_extracts_labeled_values(self):
        values = extract_labeled_values([
            "PROMISED\nPO NO\nRATE\nPAYMENT\nINV.DATE\n"
            "14JAN18 DD\n14:06 08MAY23\n174.95\nCASH\n08MAY23\n"
            "R.O. OPENED\nREADY\nOPTIONS:\nENG:1.8 Liter\n"
            "08:11 08MAY23\n11:06 08MAY23"
        ], ["Promised", "Ready"])

        self.assertEqual(values, {
            "PROMISED": ["14JAN18 DD"],
            "READY": ["11:06 08MAY23"],
        })

    def test_extracts_generic_label_values_from_neighboring_rows(self):
        values = extract_labeled_values([
            "TAX RATE\n7.5%\nINSURANCE PREMIUM\n120.00\n",
            "CLAIM NUMBER\nAB-12345\n",
        ], ["Tax Rate", "Insurance Premium", "Claim Number"])

        self.assertEqual(values, {
            "TAX RATE": ["7.5%"],
            "INSURANCE PREMIUM": ["120.00"],
            "CLAIM NUMBER": ["AB-12345"],
        })