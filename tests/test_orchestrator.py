import hashlib
import json
from unittest import TestCase

from app.ar_workflow import RemittanceWorkflow
from app.audit import AuditLedger
from app.config import Settings
from app.domain import SourceEnvelope
from app.erp import MockERP
from app.orchestrator import FinanceOrchestrator
from app.repository import MemoryRepository
from app.workflow import InvoiceWorkflow


def envelope(payload: dict | None, filename: str = "document.json") -> SourceEnvelope:
    content = json.dumps(payload).encode() if payload is not None else b"A general finance note"
    return SourceEnvelope(
        content=content,
        filename=filename if payload is not None else "note.txt",
        media_type="application/json" if payload is not None else "text/plain",
        source_ref=f"test:{filename}",
        content_sha256=hashlib.sha256(content).hexdigest(),
    )


class OrchestratorTests(TestCase):
    def setUp(self):
        repo, audit, erp, config = MemoryRepository(), AuditLedger(), MockERP(), Settings()
        self.repo = repo
        self.orchestrator = FinanceOrchestrator(
            repo,
            audit,
            InvoiceWorkflow(repo, audit, erp, config),
            RemittanceWorkflow(repo, audit, erp, config),
        )

    def test_parent_graph_dispatches_ap_to_the_ap_subgraph(self):
        response = self.orchestrator.ingest(
            envelope(
                {
                    "vendor_id": "VEND-001",
                    "invoice_number": "INV-ORCH-1",
                    "invoice_date": "2026-08-30",
                    "currency": "USD",
                    "total": "1000.00",
                    "po_number": "PO-1001",
                    "lines": [
                        {
                            "description": "Industrial sensors",
                            "quantity": "10",
                            "unit_price": "100.00",
                            "amount": "1000.00",
                            "po_line": 1,
                        }
                    ],
                },
                "invoice.json",
            )
        )
        self.assertEqual("ap", response["workflow_type"])
        self.assertTrue(response["result"]["matched"])
        self.assertEqual("ap_invoice", response["classification"]["kind"])
        self.assertEqual(
            "DISPATCH_AP",
            response["classification"]["supervisor_decision"]["action"],
        )
        self.assertEqual(
            ["RETRIEVE_POLICY", "RUN_AP_MATCH", "POST_PAYMENT_JOURNAL"],
            [decision["action"] for decision in response["agent_decisions"]],
        )

    def test_parent_graph_dispatches_ar_to_the_ar_subgraph(self):
        response = self.orchestrator.ingest(
            envelope(
                {
                    "customer_id": "CUST-001",
                    "reference": "REM-ORCH-1",
                    "amount": "1000.00",
                    "currency": "USD",
                    "open_item_refs": ["AR-9001", "AR-9002"],
                },
                "remittance.json",
            )
        )
        self.assertEqual("ar", response["workflow_type"])
        self.assertTrue(response["result"]["applied"])
        self.assertEqual("ar_remittance", response["classification"]["kind"])
        self.assertEqual(
            ["RETRIEVE_POLICY", "RUN_AR_MATCH", "APPLY_CASH"],
            [decision["action"] for decision in response["agent_decisions"]],
        )

    def test_parent_graph_routes_ambiguous_input_to_classification_review(self):
        response = self.orchestrator.ingest(envelope(None))
        self.assertEqual("classification", response["workflow_type"])
        self.assertEqual("human-classification", response["next_action"])
        self.assertEqual("classification_review", self.repo.workflow_states[response["entity_id"]]["node"])
