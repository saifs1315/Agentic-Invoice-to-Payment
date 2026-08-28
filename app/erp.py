from __future__ import annotations

from decimal import Decimal

from app.domain import Invoice, PurchaseOrder, PurchaseOrderLine, uid


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

    def apply_cash(self, customer_id: str, amount: Decimal, item_refs: list[str]) -> dict:
        items = [self.open_items.get(ref) for ref in item_refs]
        if not items or any(item is None for item in items):
            return {"applied": False, "reason": "open_item_not_found"}
        if any(item["customer_id"] != customer_id or not item["open"] for item in items):
            return {"applied": False, "reason": "customer_or_status_mismatch"}
        expected = sum((item["amount"] for item in items), Decimal("0"))
        if expected != amount:
            return {"applied": False, "reason": "amount_mismatch", "expected": str(expected), "actual": str(amount)}
        for item in items:
            item["open"] = False
        return {"applied": True, "application_id": uid("cash"), "amount": str(amount), "items": item_refs}
