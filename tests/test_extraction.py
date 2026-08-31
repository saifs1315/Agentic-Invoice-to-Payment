import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from pydantic import ValidationError

from app.extraction import _temporary_path, _text_from_payload, extract_invoice


class ExtractionTests(TestCase):
    def test_docling_is_primary_for_rich_documents(self):
        with patch("app.extraction._docling_text", return_value="converted invoice"):
            text, mode, attempts = _text_from_payload(b"<p>invoice</p>", "invoice.html")
        self.assertEqual("converted invoice", text)
        self.assertEqual("docling", mode)
        self.assertEqual("docling", attempts[0]["backend"])

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
        self.assertEqual("text", invoice.extraction_mode)
        self.assertEqual("success", invoice.extraction_attempts[-1]["outcome"])

        with _temporary_path(b"reopenable", ".bin") as filename:
            path = Path(filename)
            self.assertEqual(b"reopenable", path.read_bytes())
        self.assertFalse(path.exists())

        class FailingReader:
            def __init__(self, *args, **kwargs):
                pass

            def readtext(self, *args, **kwargs):
                raise RuntimeError("simulated OCR failure")

        with (
            patch.dict(sys.modules, {"easyocr": SimpleNamespace(Reader=FailingReader)}),
            patch("app.extraction._docling_text", side_effect=RuntimeError("no docling")),
        ):
            _, _, attempts = _text_from_payload(b"scan", "invoice.png")
        easyocr_attempts = [item for item in attempts if item["backend"] == "easyocr"]
        self.assertEqual(["failed"], [item["outcome"] for item in easyocr_attempts])

    def test_invalid_currency_is_rejected(self):
        content = b'{"vendor_id":"V1","invoice_number":"I1","invoice_date":"2026-08-22","currency":"US","total":"1","lines":[]}'
        with self.assertRaises(ValidationError):
            extract_invoice(content, "invoice.json", "test:invalid")

        excessive = b'{"vendor_id":"V1","invoice_number":"I2","invoice_date":"2026-08-22","currency":"USD","total":"999999999999999999999999999","lines":[]}'
        with self.assertRaises(ValidationError):
            extract_invoice(excessive, "invoice.json", "test:excessive")

    def test_tax_and_subtotal_are_preserved_for_reconciliation(self):
        content = b'''{
          "vendor_id":"VEND-001","invoice_number":"INV-TAX-1",
          "invoice_date":"2026-08-22","currency":"USD","subtotal":"1000.00",
          "tax_amount":"80.00","total":"1080.00","po_number":"PO-1001",
          "lines":[{"description":"Industrial sensors","quantity":"10",
          "unit_price":"100.00","amount":"1000.00","po_line":1}]}
        '''
        invoice = extract_invoice(content, "tax.json", "test:tax")
        self.assertEqual("1000.00", str(invoice.subtotal))
        self.assertEqual("80.00", str(invoice.tax_amount))
        self.assertEqual("json:subtotal", invoice.evidence["subtotal"])
        self.assertEqual("json:tax_amount", invoice.evidence["tax_amount"])
