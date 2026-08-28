from decimal import Decimal
from unittest import TestCase

from app.erp import MockERP


class ARTests(TestCase):
    def test_exact_cash_application(self):
        result = MockERP().apply_cash("CUST-001", Decimal("1000.00"), ["AR-9001", "AR-9002"])
        self.assertTrue(result["applied"])

    def test_partial_payment_is_exception(self):
        result = MockERP().apply_cash("CUST-001", Decimal("900.00"), ["AR-9001", "AR-9002"])
        self.assertEqual("amount_mismatch", result["reason"])

