from unittest import TestCase

from fastapi.testclient import TestClient

import app.mock_erp_api as mock_api
from app.erp import MockERP


class MockERPAPITests(TestCase):
    def setUp(self):
        mock_api.erp = MockERP()
        self.client = TestClient(mock_api.app)

    def test_po_goods_receipt_and_journal_contracts(self):
        po = self.client.get("/erp/v1/purchase-orders/PO-1001")
        receipt = self.client.get("/erp/v1/purchase-orders/PO-1001/goods-receipts")
        self.assertEqual(200, po.status_code)
        self.assertEqual("10", receipt.json()["lines"][0]["received_quantity"])
        payload = {
            "invoice_id": "inv_http_1",
            "vendor_id": "VEND-001",
            "amount": "1000.00",
            "currency": "USD",
            "po_number": "PO-1001",
        }
        first = self.client.post(
            "/erp/v1/payment-journals",
            headers={"Idempotency-Key": "journal-http-1"},
            json=payload,
        )
        second = self.client.post(
            "/erp/v1/payment-journals",
            headers={"Idempotency-Key": "journal-http-1"},
            json=payload,
        )
        self.assertEqual(first.json()["journal_id"], second.json()["journal_id"])

    def test_cash_api_revalidates_and_is_idempotent(self):
        payload = {
            "remittance_id": "rem_http_1",
            "customer_id": "CUST-001",
            "amount": "1000.00",
            "currency": "USD",
            "open_item_refs": ["AR-9001", "AR-9002"],
        }
        first = self.client.post(
            "/erp/v1/cash-applications",
            headers={"Idempotency-Key": "cash-http-1"},
            json=payload,
        )
        second = self.client.post(
            "/erp/v1/cash-applications",
            headers={"Idempotency-Key": "cash-http-1"},
            json=payload,
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual(first.json()["application_id"], second.json()["application_id"])

        mismatch = self.client.post(
            "/erp/v1/cash-applications",
            headers={"Idempotency-Key": "cash-http-2"},
            json={**payload, "remittance_id": "rem_http_2", "currency": "EUR"},
        )
        self.assertEqual(409, mismatch.status_code)
