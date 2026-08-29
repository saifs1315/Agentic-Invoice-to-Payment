from decimal import Decimal
from unittest import TestCase

from app.erp import MockERP


class ARTests(TestCase):
    def test_exact_cash_application(self):
        result = MockERP().apply_cash(
            "CUST-001",
            Decimal("1000.00"),
            "USD",
            ["AR-9001", "AR-9002"],
        )
        self.assertTrue(result["applied"])

    def test_partial_payment_is_exception(self):
        result = MockERP().apply_cash(
            "CUST-001",
            Decimal("900.00"),
            "USD",
            ["AR-9001", "AR-9002"],
        )
        self.assertEqual("amount_mismatch", result["reason"])

    def test_currency_mismatch_is_exception(self):
        result = MockERP().apply_cash(
            "CUST-001",
            Decimal("1000.00"),
            "EUR",
            ["AR-9001", "AR-9002"],
        )

        self.assertFalse(result["applied"])
        self.assertEqual("currency_mismatch", result["reason"])
        self.assertEqual("USD", result["expected"])
        self.assertEqual("EUR", result["actual"])
