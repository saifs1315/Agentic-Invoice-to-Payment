from __future__ import annotations

from decimal import Decimal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.domain import Invoice
from app.erp import MockERP


app = FastAPI(
    title="LedgerPilot Mock ERP API",
    version="1.0.0",
    description="A stateful HTTP sandbox for PO, goods receipt, journal, and cash APIs.",
)
erp = MockERP()


class PaymentJournalRequest(BaseModel):
    invoice_id: str
    vendor_id: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    po_number: str | None = None
    approved_exception: bool = False


class CashApplicationRequest(BaseModel):
    remittance_id: str | None = None
    customer_id: str
    amount: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    open_item_refs: list[str] = Field(min_length=1)


@app.get("/erp/v1/health")
def health() -> dict:
    return {"status": "ok", "system": "ledgerpilot-mock-erp"}


@app.get("/erp/v1/purchase-orders/{po_number}")
def purchase_order(po_number: str) -> dict:
    po = erp.get_purchase_order(po_number)
    if po is None:
        raise HTTPException(404, "purchase order not found")
    return {
        "number": po.number,
        "vendor_id": po.vendor_id,
        "currency": po.currency,
        "lines": [
            {
                "line_number": line.line_number,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit_price": str(line.unit_price),
            }
            for line in po.lines
        ],
    }


@app.get("/erp/v1/purchase-orders/{po_number}/goods-receipts")
def goods_receipts(po_number: str) -> dict:
    po = erp.get_purchase_order(po_number)
    if po is None:
        raise HTTPException(404, "purchase order not found")
    return {
        "po_number": po.number,
        "lines": [
            {
                "line_number": line.line_number,
                "received_quantity": str(line.received_quantity),
            }
            for line in po.lines
        ],
    }


@app.post("/erp/v1/payment-journals")
def payment_journal(
    request: PaymentJournalRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict:
    po = erp.get_purchase_order(request.po_number)
    approved_non_po = request.po_number is None and request.approved_exception
    if not approved_non_po and (
        po is None or po.vendor_id != request.vendor_id or po.currency != request.currency
    ):
        raise HTTPException(409, "ERP revalidation failed for PO, vendor, or currency")
    invoice = Invoice(
        vendor_id=request.vendor_id,
        invoice_number=request.invoice_id,
        invoice_date=__import__("datetime").date.today(),
        currency=request.currency,
        total=request.amount,
        po_number=request.po_number,
        lines=[],
        source_ref="mock-erp-api",
        id=request.invoice_id,
    )
    journal = erp.post_payment_journal(invoice, idempotency_key)
    return {**journal, "approved_exception": approved_non_po}


@app.get("/erp/v1/customers/{customer_id}/open-items")
def open_items(customer_id: str) -> dict:
    items = erp.get_open_items(customer_id)
    if not items:
        raise HTTPException(404, "customer has no open items")
    return {"customer_id": customer_id, "items": items}


@app.post("/erp/v1/cash-applications")
def cash_application(
    request: CashApplicationRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
) -> dict:
    result = erp.apply_cash(
        request.customer_id,
        request.amount,
        request.currency,
        request.open_item_refs,
        idempotency_key,
        request.remittance_id,
    )
    if not result["applied"]:
        raise HTTPException(409, result)
    return result
