import json
from pathlib import Path
from unittest import TestCase

from app.audit import AuditLedger
from app.config import Settings
from app.erp import ERPUnavailableError, MockERP
from app.extraction import extract_invoice
from app.domain import Status
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
        self.assertIn(self.invoice.id, self.repo.workflow_states)
        self.assertEqual(self.repo.workflow_states[self.invoice.id]["node"], "auto_post")
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

    def test_human_approval_gate_blocks_posting_until_approved(self):
        workflow = InvoiceWorkflow(
            self.repo,
            self.audit,
            self.erp,
            Settings(require_human_approval=True, auto_post_enabled=True),
        )
        workflow.ingest(self.invoice)
        matched = workflow.match(self.invoice.id)
        self.assertEqual("human-review", matched["next_action"])
        self.assertEqual(Status.AWAITING_APPROVAL, self.invoice.status)
        with self.assertRaises(ValueError):
            workflow.post(self.invoice.id, "approval-required")
        workflow.approve(self.invoice.id, "reviewer:test", True, "Validated against evidence")
        journal = workflow.post(self.invoice.id, "approval-required")
        self.assertEqual("posted", journal["status"])

    def test_posted_invoice_cannot_be_regressed_by_rematching(self):
        self.workflow.ingest(self.invoice)
        self.workflow.match(self.invoice.id)
        self.assertEqual(Status.POSTED, self.invoice.status)

        with self.assertRaisesRegex(ValueError, "terminal status posted"):
            self.workflow.match(self.invoice.id, require_goods_receipt=False)

        self.assertEqual(Status.POSTED, self.invoice.status)
        self.assertEqual([], self.repo.list_invoices(Status.EXCEPTION))

    def test_erp_posting_failure_is_audited_and_persisted(self):
        class FailingERP(MockERP):
            def post_payment_journal(self, invoice, idempotency_key):
                raise ERPUnavailableError("ERP API unavailable (503)")

        workflow = InvoiceWorkflow(
            self.repo,
            self.audit,
            FailingERP(),
            Settings(auto_post_enabled=False),
        )
        workflow.ingest(self.invoice)
        workflow.match(self.invoice.id)

        with self.assertRaises(ERPUnavailableError):
            workflow.post(self.invoice.id, "erp-failure")

        events = self.audit.list(self.invoice.id)
        self.assertEqual("payment_journal_blocked", events[-1]["action"])
        self.assertEqual("payment_journal_blocked", self.repo.workflow_states[self.invoice.id]["node"])
        self.assertEqual(Status.MATCHED, self.invoice.status)
