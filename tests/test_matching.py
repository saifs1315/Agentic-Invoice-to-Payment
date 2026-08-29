from datetime import date
from decimal import Decimal
from unittest import TestCase

from app.domain import Invoice, InvoiceLine
from app.erp import MockERP
from app.matching import match_invoice


def invoice(price: str = "100.00", quantity: str = "10", number: str = "INV-1") -> Invoice:
    qty, unit = Decimal(quantity), Decimal(price)
    return Invoice("VEND-001", number, date(2026, 8, 20), "USD", qty * unit, "PO-1001", [InvoiceLine("Industrial sensors", qty, unit, qty * unit, 1)], "test")


class MatchingTests(TestCase):
    def setUp(self) -> None:
        self.po = MockERP().get_purchase_order("PO-1001")

    def test_three_way_happy_path(self):
        result = match_invoice(invoice(), self.po)
        self.assertTrue(result.matched)
        self.assertEqual([], result.variances)

    def test_boundary_is_in_tolerance(self):
        result = match_invoice(invoice(price="102.00"), self.po, price_tolerance_pct=2, total_tolerance_pct=2)
        self.assertTrue(result.matched)

    def test_price_outside_tolerance_is_exception(self):
        result = match_invoice(invoice(price="102.01"), self.po, price_tolerance_pct=2, total_tolerance_pct=2)
        self.assertFalse(result.matched)
        self.assertIn("PRICE_VARIANCE", {v.code for v in result.variances})

    def test_goods_receipt_shortfall(self):
        po = MockERP().get_purchase_order("PO-1002")
        inv = Invoice("VEND-002", "INV-2", date(2026, 8, 20), "USD", Decimal("3000"), "PO-1002", [InvoiceLine("Consulting hours", Decimal("20"), Decimal("150"), Decimal("3000"), 1)], "test")
        result = match_invoice(inv, po)
        self.assertIn("RECEIPT_SHORTFALL", {v.code for v in result.variances})

    def test_missing_po(self):
        result = match_invoice(invoice(), None)
        self.assertEqual("MISSING_PO", result.variances[0].code)

    def test_partial_invoice_matches_invoiced_subset(self):
        result = match_invoice(invoice(quantity="5"), self.po)
        self.assertTrue(result.matched)
        self.assertEqual([], result.variances)

    def test_tax_is_reconciled_separately_from_goods(self):
        inv = invoice()
        inv.subtotal = Decimal("1000.00")
        inv.tax_amount = Decimal("80.00")
        inv.total = Decimal("1080.00")
        result = match_invoice(inv, self.po)
        self.assertTrue(result.matched)

    def test_inconsistent_line_arithmetic_is_blocked(self):
        inv = invoice()
        inv.lines[0].amount = Decimal("9999.00")
        inv.total = Decimal("9999.00")
        result = match_invoice(inv, self.po)
        self.assertFalse(result.matched)
        self.assertIn("LINE_AMOUNT_MISMATCH", {variance.code for variance in result.variances})

    def test_invoice_total_reconciliation_is_blocking(self):
        inv = invoice()
        inv.total = Decimal("1001.00")
        result = match_invoice(inv, self.po)
        self.assertFalse(result.matched)
        self.assertIn("INVOICE_TOTAL_MISMATCH", {variance.code for variance in result.variances})
