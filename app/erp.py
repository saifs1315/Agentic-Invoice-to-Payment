from __future__ import annotations

from decimal import Decimal
from typing import Protocol

import httpx

from app.domain import Invoice, PurchaseOrder, PurchaseOrderLine, Status, uid


class ERPError(RuntimeError):
    """Base exception for controlled ERP boundary failures."""


class ERPConflictError(ERPError):
    """The ERP rejected a request because current business state conflicts."""


class ERPUnavailableError(ERPError):
    """The ERP could not be reached or returned a server-side failure."""


class ERPClient(Protocol):
    def get_purchase_order(self, number: str | None) -> PurchaseOrder | None: ...

    def post_payment_journal(self, invoice: Invoice, idempotency_key: str) -> dict: ...

    def get_open_items(self, customer_id: str) -> list[dict]: ...

    def apply_cash(
        self,
        customer_id: str,
        amount: Decimal,
        currency: str,
        item_refs: list[str],
        idempotency_key: str | None = None,
        remittance_id: str | None = None,
    ) -> dict: ...


class MockERP:
    def __init__(self) -> None:
        self.purchase_orders = {
            "PO-1001": PurchaseOrder("PO-1001", "VEND-001", "USD", [PurchaseOrderLine(1, "Industrial sensors", Decimal("10"), Decimal("100.00"), Decimal("10"))]),
            "PO-1002": PurchaseOrder("PO-1002", "VEND-002", "USD", [PurchaseOrderLine(1, "Consulting hours", Decimal("20"), Decimal("150.00"), Decimal("15"))]),
        }
        self.open_items = {
            "AR-9001": {"customer_id": "CUST-001", "amount": Decimal("750.00"), "currency": "USD", "open": True},
            "AR-9002": {"customer_id": "CUST-001", "amount": Decimal("250.00"), "currency": "USD", "open": True},
        }
        self._posted_by_key: dict[str, dict] = {}
        self._posted_by_invoice: dict[str, dict] = {}
        self._cash_by_key: dict[str, dict] = {}
        self._cash_by_remittance: dict[str, dict] = {}

    def get_purchase_order(self, number: str | None) -> PurchaseOrder | None:
        return self.purchase_orders.get(number or "")

    def post_payment_journal(self, invoice: Invoice, idempotency_key: str) -> dict:
        if idempotency_key in self._posted_by_key:
            return self._posted_by_key[idempotency_key]
        if invoice.id in self._posted_by_invoice:
            journal = self._posted_by_invoice[invoice.id]
            self._posted_by_key[idempotency_key] = journal
            return journal
        journal = {"journal_id": uid("pj"), "invoice_id": invoice.id, "vendor_id": invoice.vendor_id, "amount": str(invoice.total), "currency": invoice.currency, "status": "posted", "idempotency_key": idempotency_key}
        self._posted_by_key[idempotency_key] = journal
        self._posted_by_invoice[invoice.id] = journal
        return journal

    def get_open_items(self, customer_id: str) -> list[dict]:
        return [
            {"reference": reference, **item, "amount": str(item["amount"])}
            for reference, item in self.open_items.items()
            if item["customer_id"] == customer_id and item["open"]
        ]

    def apply_cash(
        self,
        customer_id: str,
        amount: Decimal,
        currency: str,
        item_refs: list[str],
        idempotency_key: str | None = None,
        remittance_id: str | None = None,
    ) -> dict:
        key = idempotency_key or f"cash:{remittance_id or customer_id}:{','.join(item_refs)}:{amount}"
        if key in self._cash_by_key:
            return self._cash_by_key[key]
        if remittance_id and remittance_id in self._cash_by_remittance:
            result = self._cash_by_remittance[remittance_id]
            self._cash_by_key[key] = result
            return result
        items = [self.open_items.get(ref) for ref in item_refs]
        if not items or any(item is None for item in items):
            return {"applied": False, "reason": "open_item_not_found"}
        if any(item["customer_id"] != customer_id or not item["open"] for item in items):
            return {"applied": False, "reason": "customer_or_status_mismatch"}
        expected_currencies = sorted({str(item["currency"]) for item in items})
        if any(item["currency"] != currency for item in items):
            return {
                "applied": False,
                "reason": "currency_mismatch",
                "expected": ",".join(expected_currencies),
                "actual": currency,
            }
        expected = sum((item["amount"] for item in items), Decimal("0"))
        if expected != amount:
            return {"applied": False, "reason": "amount_mismatch", "expected": str(expected), "actual": str(amount)}
        for item in items:
            item["open"] = False
        result = {
            "applied": True,
            "application_id": uid("cash"),
            "amount": str(amount),
            "currency": currency,
            "items": item_refs,
            "idempotency_key": key,
            "remittance_id": remittance_id,
        }
        self._cash_by_key[key] = result
        if remittance_id:
            self._cash_by_remittance[remittance_id] = result
        return result


class HttpERPClient:
    """HTTP boundary used by the application container to call the Mock ERP API."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout,
                **kwargs,
            )
        except httpx.RequestError as exc:
            raise ERPUnavailableError(f"ERP API request failed: {type(exc).__name__}") from exc
        if response.status_code >= 500:
            raise ERPUnavailableError(f"ERP API unavailable ({response.status_code})")
        return response

    @staticmethod
    def _conflict(response: httpx.Response) -> ERPConflictError:
        try:
            payload = response.json()
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        except ValueError:
            detail = response.text or f"HTTP {response.status_code}"
        return ERPConflictError(f"ERP rejected request: {detail}")

    def get_purchase_order(self, number: str | None) -> PurchaseOrder | None:
        if not number:
            return None
        po_response = self._request("GET", f"/erp/v1/purchase-orders/{number}")
        if po_response.status_code == 404:
            return None
        if po_response.status_code >= 400:
            raise self._conflict(po_response)
        receipt_response = self._request(
            "GET", f"/erp/v1/purchase-orders/{number}/goods-receipts"
        )
        if receipt_response.status_code >= 400:
            raise self._conflict(receipt_response)
        receipts = {
            int(line["line_number"]): Decimal(str(line["received_quantity"]))
            for line in receipt_response.json()["lines"]
        }
        payload = po_response.json()
        return PurchaseOrder(
            number=payload["number"],
            vendor_id=payload["vendor_id"],
            currency=payload["currency"],
            lines=[
                PurchaseOrderLine(
                    line_number=int(line["line_number"]),
                    description=line["description"],
                    quantity=Decimal(str(line["quantity"])),
                    unit_price=Decimal(str(line["unit_price"])),
                    received_quantity=receipts.get(int(line["line_number"]), Decimal("0")),
                )
                for line in payload["lines"]
            ],
        )

    def post_payment_journal(self, invoice: Invoice, idempotency_key: str) -> dict:
        response = self._request(
            "POST",
            "/erp/v1/payment-journals",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "invoice_id": invoice.id,
                "vendor_id": invoice.vendor_id,
                "amount": str(invoice.total),
                "currency": invoice.currency,
                "po_number": invoice.po_number,
                "approved_exception": invoice.status == Status.APPROVED,
            },
        )
        if response.status_code >= 400:
            raise self._conflict(response)
        return response.json()

    def get_open_items(self, customer_id: str) -> list[dict]:
        response = self._request("GET", f"/erp/v1/customers/{customer_id}/open-items")
        if response.status_code == 404:
            return []
        if response.status_code >= 400:
            raise self._conflict(response)
        return response.json()["items"]

    def apply_cash(
        self,
        customer_id: str,
        amount: Decimal,
        currency: str,
        item_refs: list[str],
        idempotency_key: str | None = None,
        remittance_id: str | None = None,
    ) -> dict:
        response = self._request(
            "POST",
            "/erp/v1/cash-applications",
            headers={"Idempotency-Key": idempotency_key or f"cash:{remittance_id}"},
            json={
                "remittance_id": remittance_id,
                "customer_id": customer_id,
                "amount": str(amount),
                "currency": currency,
                "open_item_refs": item_refs,
            },
        )
        if response.status_code == 409:
            return response.json().get("detail", response.json())
        if response.status_code >= 400:
            raise self._conflict(response)
        return response.json()
