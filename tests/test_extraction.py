from unittest import TestCase

from pydantic import ValidationError

from app.extraction import extract_invoice


class ExtractionTests(TestCase):
    def test_text_invoice_is_schema_validated_with_lines(self):
        content = b"""Vendor ID: VEND-001
Invoice Number: INV-TEXT-01
Invoice Date: 2026-08-22
PO Number: PO-1001
Currency: USD
Line 1: Industrial sensors | Qty: 10 | Unit Price: 100.00 | Amount: 1000.00 | PO Line: 1
Invoice Total: 1000.00"""
        invoice = extract_invoice(content, "invoice.txt", "test:text")
        self.assertEqual(invoice.invoice_number, "INV-TEXT-01")
        self.assertEqual(str(invoice.lines[0].quantity), "10")
        self.assertEqual(str(invoice.lines[0].unit_price), "100.00")

    def test_invalid_currency_is_rejected(self):
        content = b'{"vendor_id":"V1","invoice_number":"I1","invoice_date":"2026-08-22","currency":"US","total":"1","lines":[]}'
        with self.assertRaises(ValidationError):
            extract_invoice(content, "invoice.json", "test:invalid")
