import json
from unittest import TestCase

from fastapi.testclient import TestClient

from app.api import app


class APITests(TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_and_unknown_invoice_contracts(self):
        health = self.client.get("/api/v1/health")
        self.assertEqual(200, health.status_code)
        self.assertTrue(health.json()["audit_chain_valid"])
        missing = self.client.post(
            "/api/v1/post-payment-journal",
            headers={"Idempotency-Key": "missing"},
            json={"invoice_id": "inv_missing"},
        )
        self.assertEqual(404, missing.status_code)

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
