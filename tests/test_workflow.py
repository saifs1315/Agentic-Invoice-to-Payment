import json
from pathlib import Path
from unittest import TestCase

from app.audit import AuditLedger
from app.config import Settings
from app.erp import MockERP
from app.extraction import extract_invoice
from app.repository import MemoryRepository
from app.workflow import InvoiceWorkflow


FIXTURE = Path(__file__).parents[1] / "evaluation" / "fixtures" / "po-1001-invoice.json"


class WorkflowTests(TestCase):
    def setUp(self):
        self.repo, self.audit, self.erp = MemoryRepository(), AuditLedger(), MockERP()
        self.workflow = InvoiceWorkflow(self.repo, self.audit, self.erp, Settings())
        self.invoice = extract_invoice(FIXTURE.read_bytes(), FIXTURE.name, "test")

    def test_end_to_end_and_idempotency(self):
        self.workflow.ingest(self.invoice)
        matched = self.workflow.match(self.invoice.id)
        self.assertTrue(matched["result"]["matched"])
        first = self.workflow.post(self.invoice.id, "same-key")
        second = self.workflow.post(self.invoice.id, "same-key")
        self.assertEqual(first["journal_id"], second["journal_id"])
        self.assertTrue(self.audit.verify())

    def test_duplicate_detection(self):
        self.workflow.ingest(self.invoice)
        duplicate_data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        duplicate = extract_invoice(json.dumps(duplicate_data).encode(), "duplicate.json", "test-2")
        self.workflow.ingest(duplicate)
        result = self.workflow.match(duplicate.id)["result"]
        self.assertIn("DUPLICATE_INVOICE", {v["code"] for v in result["variances"]})

