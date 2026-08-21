import unittest

from archive_manager.domain.arithmetic import reconcile_line_totals


class ArithmeticTest(unittest.TestCase):
    def test_matching_line_totals(self):
        result = reconcile_line_totals({"A": "10.00", "B": "5.00"}, "15.00")

        self.assertEqual(result["status"], "matched")

    def test_mismatch_is_reported_not_replaced(self):
        result = reconcile_line_totals({"A": "10.00"}, "12.00")

        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["declared_total"], "12.00")

    def test_missing_inputs_are_not_checked(self):
        self.assertEqual(reconcile_line_totals({}, "12.00")["status"], "not_checked")


if __name__ == "__main__":
    unittest.main()