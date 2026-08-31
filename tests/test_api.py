import json
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.api import app
from app.bootstrap import agent_runtime, audit, workflow
from app.config import settings
from app.erp import ERPUnavailableError


class APITests(TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_and_unknown_invoice_contracts(self):
        health = self.client.get("/api/v1/health")
        self.assertEqual(200, health.status_code)
        self.assertTrue(health.json()["audit_chain_valid"])
        self.assertTrue(health.json()["capabilities"]["langgraph_active"])
        self.assertTrue(health.json()["capabilities"]["llamaindex_active"])
        missing = self.client.post(
            "/api/v1/post-payment-journal",
            headers={"Idempotency-Key": "missing"},
            json={"invoice_id": "inv_missing"},
        )
        self.assertEqual(404, missing.status_code)

    def test_health_is_unavailable_when_mandatory_ai_models_are_not_ready(self):
        unavailable = {
            "runtime": "ollama",
            "reachable": True,
            "generation_model": "qwen3.5:2b-q4_K_M",
            "generation_model_ready": False,
            "embedding_model": "embeddinggemma",
            "embedding_model_ready": True,
            "ready": False,
        }
        with patch.object(agent_runtime, "capabilities", return_value=unavailable):
            health = self.client.get("/api/v1/health")
        self.assertEqual(503, health.status_code)
        self.assertEqual("unavailable", health.json()["status"])
        self.assertTrue(health.json()["capabilities"]["ai_required"])

    def test_posted_invoice_cannot_be_rematched_through_api(self):
        payload = {
            "vendor_id": "VEND-001",
            "invoice_number": "INV-API-TERMINAL-GUARD",
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
        }
        ingested = self.client.post(
            "/api/v1/ingest-invoice",
            files={"file": ("invoice.json", json.dumps(payload), "application/json")},
        )
        invoice_id = ingested.json()["invoice"]["id"]
        first_match = self.client.post(
            "/api/v1/match-po",
            json={"invoice_id": invoice_id, "require_goods_receipt": True},
        )
        rematch = self.client.post(
            "/api/v1/match-po",
            json={"invoice_id": invoice_id, "require_goods_receipt": False},
        )

        self.assertEqual(200, first_match.status_code)
        self.assertEqual(409, rematch.status_code)
        exceptions = self.client.get("/api/v1/exceptions").json()
        self.assertNotIn(invoice_id, {item["id"] for item in exceptions})

    def test_erp_failure_returns_503_and_is_audited(self):
        payload = {
            "vendor_id": "VEND-001",
            "invoice_number": "INV-API-ERP-FAILURE",
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
        }
        ingested = self.client.post(
            "/api/v1/ingest-invoice",
            files={"file": ("invoice.json", json.dumps(payload), "application/json")},
        )
        invoice_id = ingested.json()["invoice"]["id"]
        with patch.object(
            workflow.erp,
            "post_payment_journal",
            side_effect=ERPUnavailableError("ERP API unavailable (503)"),
        ):
            failed = self.client.post(
                "/api/v1/match-po",
                json={"invoice_id": invoice_id, "require_goods_receipt": True},
            )

        self.assertEqual(503, failed.status_code)
        actions = {event["action"] for event in audit.list(invoice_id)}
        self.assertIn("payment_journal_blocked", actions)

    def test_upload_limit_rejects_content_beyond_configured_size(self):
        oversized = b"x" * (settings.max_upload_mb * 1024 * 1024 + 1)
        response = self.client.post(
            "/api/v1/ingest-document",
            files={"file": ("oversized.txt", oversized, "text/plain")},
        )
        self.assertEqual(413, response.status_code)

    def test_ingest_match_post_and_audit_api(self):
        payload = {
            "vendor_id": "VEND-001",
            "invoice_number": "INV-API-UNIQUE",
            "invoice_date": "2026-08-25",
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
        }
        ingested = self.client.post(
            "/api/v1/ingest-invoice",
            files={"file": ("invoice.json", json.dumps(payload), "application/json")},
        )
        self.assertEqual(202, ingested.status_code)
        invoice_id = ingested.json()["invoice"]["id"]
        matched = self.client.post(
            "/api/v1/match-po",
            json={"invoice_id": invoice_id, "require_goods_receipt": True},
        )
        self.assertEqual(200, matched.status_code)
        self.assertTrue(matched.json()["result"]["matched"])
        key = f"auto:{invoice_id}"
        posted = self.client.post(
            "/api/v1/post-payment-journal",
            headers={"Idempotency-Key": key},
            json={"invoice_id": invoice_id},
        )
        self.assertEqual(200, posted.status_code)
        audit = self.client.get("/api/v1/audit-log", params={"entity_id": invoice_id})
        self.assertEqual(200, audit.status_code)
        self.assertTrue(audit.json()["chain_valid"])
        self.assertGreaterEqual(len(audit.json()["events"]), 3)

        excessive = {**payload, "invoice_number": "INV-API-EXCESSIVE", "total": "9" * 27}
        rejected = self.client.post(
            "/api/v1/ingest-invoice",
            files={"file": ("invoice.json", json.dumps(excessive), "application/json")},
        )
        self.assertEqual(422, rejected.status_code)

    def test_ar_currency_exception_is_visible_to_operators(self):
        created = self.client.post(
            "/api/v1/ingest-remittance",
            json={
                "customer_id": "CUST-001",
                "reference": "REM-API-CURRENCY-MISMATCH",
                "amount": "1000.00",
                "currency": "EUR",
                "open_item_refs": ["AR-9001", "AR-9002"],
                "source_ref": "api:test-ar-exception",
            },
        )
        self.assertEqual(202, created.status_code)
        self.assertEqual("exception", created.json()["remittance"]["status"])

        queue = self.client.get("/api/v1/remittance-exceptions")
        self.assertEqual(200, queue.status_code)
        record = next(
            item
            for item in queue.json()
            if item["remittance"]["id"] == created.json()["remittance"]["id"]
        )
        self.assertEqual("currency_mismatch", record["result"]["reason"])
        self.assertEqual("USD", record["result"]["expected"])
        self.assertEqual("EUR", record["result"]["actual"])

    def test_remittance_amount_limit_is_enforced(self):
        rejected = self.client.post(
            "/api/v1/ingest-remittance",
            json={
                "customer_id": "CUST-001",
                "reference": "REM-API-OVERSIZED",
                "amount": "1000000000.01",
                "currency": "USD",
                "open_item_refs": ["AR-9001"],
            },
        )
        self.assertEqual(422, rejected.status_code)

    def test_unified_document_endpoint_dispatches_and_reports_workflow_state(self):
        payload = {
            "vendor_id": "VEND-001",
            "invoice_number": "INV-UNIFIED-API",
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
        }
        response = self.client.post(
            "/api/v1/ingest-document",
            files={"file": ("invoice.json", json.dumps(payload), "application/json")},
        )
        self.assertEqual(202, response.status_code)
        body = response.json()
        self.assertEqual("ap", body["workflow_type"])
        self.assertEqual("ap_invoice", body["classification"]["kind"])
        state = self.client.get(f"/api/v1/workflows/{body['entity_id']}")
        self.assertEqual(200, state.status_code)
        self.assertEqual("ap", state.json()["workflow_type"])

    def test_unified_document_endpoint_routes_ambiguity_to_review(self):
        response = self.client.post(
            "/api/v1/ingest-document",
            files={"file": ("note.txt", b"General finance note", "text/plain")},
        )
        self.assertEqual(202, response.status_code)
        self.assertEqual("classification", response.json()["workflow_type"])
        self.assertEqual("human-classification", response.json()["next_action"])
