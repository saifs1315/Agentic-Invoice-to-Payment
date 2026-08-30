from decimal import Decimal
from unittest import TestCase

from app.ar_workflow import RemittanceWorkflow
from app.audit import AuditLedger
from app.config import Settings
from app.domain import Remittance, Status
from app.erp import MockERP
from app.repository import MemoryRepository


def remittance(reference: str, amount: str = "1000.00", currency: str = "USD") -> Remittance:
    return Remittance(
        "CUST-001",
        reference,
        Decimal(amount),
        currency,
        ["AR-9001", "AR-9002"],
        "test:ar-workflow",
        confidence=1.0,
    )


class ARWorkflowTests(TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.audit = AuditLedger()
        self.erp = MockERP()
        self.workflow = RemittanceWorkflow(self.repo, self.audit, self.erp, Settings())

    def test_exact_remittance_runs_graph_and_applies_cash_idempotently(self):
        item = remittance("REM-EXACT")
        result = self.workflow.ingest(item, run=True)
        self.assertTrue(result["result"]["matched"])
        self.assertTrue(result["result"]["applied"])
        self.assertEqual(Status.POSTED, item.status)
        self.assertEqual("apply_cash", self.repo.workflow_states[item.id]["node"])
        replay = self.erp.apply_cash(
            item.customer_id,
            item.amount,
            item.currency,
            item.open_item_refs,
            f"auto:{item.id}",
            item.id,
        )
        self.assertEqual(result["result"]["application_id"], replay["application_id"])
        self.assertTrue(self.audit.verify())

    def test_partial_payment_requires_correction_and_full_rematch(self):
        item = remittance("REM-PARTIAL", "900.00")
        result = self.workflow.ingest(item, run=True)
        self.assertEqual(Status.EXCEPTION, item.status)
        self.assertEqual("amount_mismatch", result["result"]["reason"])
        corrected = self.workflow.decide(
            item.id,
            "RETRY_WITH_CORRECTION",
            "reviewer:test",
            "Confirmed bank amount",
            {"amount": "1000.00"},
        )
        self.assertTrue(corrected["result"]["applied"])
        self.assertEqual(Status.POSTED, item.status)

    def test_human_approval_can_only_apply_a_valid_match(self):
        controlled = RemittanceWorkflow(
            self.repo,
            self.audit,
            self.erp,
            Settings(require_human_approval=True, auto_post_enabled=True),
        )
        item = remittance("REM-APPROVAL")
        result = controlled.ingest(item, run=True)
        self.assertTrue(result["result"]["matched"])
        self.assertEqual(Status.AWAITING_APPROVAL, item.status)
        approved = controlled.decide(
            item.id,
            "APPROVE_APPLY",
            "reviewer:test",
            "Evidence verified",
        )
        self.assertTrue(approved["result"]["applied"])

    def test_invalid_match_cannot_be_force_approved(self):
        item = remittance("REM-BAD", currency="EUR")
        self.workflow.ingest(item, run=True)
        with self.assertRaisesRegex(ValueError, "only a valid matched remittance"):
            self.workflow.decide(
                item.id,
                "APPROVE_APPLY",
                "reviewer:test",
                "Do not bypass controls",
            )
