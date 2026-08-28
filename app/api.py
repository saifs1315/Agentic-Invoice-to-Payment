from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.bootstrap import audit, erp, repo, workflow
from app.config import settings
from app.domain import Status
from app.extraction import extract_invoice, extract_remittance

app = FastAPI(title="LedgerPilot API", version="0.1.0", description="Auditable agentic invoice-to-payment and remittance automation")


class MatchRequest(BaseModel):
    invoice_id: str
    require_goods_receipt: bool = True


class PostRequest(BaseModel):
    invoice_id: str


class DecisionRequest(BaseModel):
    invoice_id: str
    approved: bool
    actor: str = Field(min_length=3)
    comment: str = Field(min_length=3)


class RemittanceRequest(BaseModel):
    customer_id: str
    reference: str
    amount: str
    currency: str = "USD"
    open_item_refs: list[str]
    source_ref: str = "api"


@app.post("/api/v1/ingest-invoice", status_code=202, tags=["AP"])
async def ingest_invoice(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "attachment exceeds configured maximum")
    source_hash = hashlib.sha256(content).hexdigest()
    try:
        invoice = extract_invoice(content, file.filename or "invoice.bin", f"sha256:{source_hash}")
        return workflow.ingest(invoice)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v1/match-po", tags=["AP"])
def match_po(request: MatchRequest) -> dict[str, Any]:
    if request.invoice_id not in repo.invoices:
        raise HTTPException(404, "invoice not found")
    return workflow.match(request.invoice_id, request.require_goods_receipt)


@app.post("/api/v1/post-payment-journal", tags=["AP"])
def post_payment_journal(request: PostRequest, idempotency_key: str = Header(..., alias="Idempotency-Key")) -> dict[str, Any]:
    try:
        return workflow.post(request.invoice_id, idempotency_key)
    except KeyError as exc:
        raise HTTPException(404, "invoice not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/v1/exceptions/decision", tags=["Human oversight"])
def exception_decision(request: DecisionRequest) -> dict[str, Any]:
    if request.invoice_id not in repo.invoices:
        raise HTTPException(404, "invoice not found")
    return workflow.approve(request.invoice_id, request.actor, request.approved, request.comment)


@app.get("/api/v1/exceptions", tags=["Human oversight"])
def list_exceptions() -> list[dict[str, Any]]:
    return [invoice.to_dict() for invoice in repo.invoices.values() if invoice.status == Status.EXCEPTION]


@app.post("/api/v1/ingest-remittance", status_code=202, tags=["AR"])
def ingest_remittance(request: RemittanceRequest) -> dict[str, Any]:
    remittance = extract_remittance(request.model_dump(), request.source_ref)
    repo.remittances[remittance.id] = remittance
    result = erp.apply_cash(remittance.customer_id, remittance.amount, remittance.open_item_refs)
    remittance.status = Status.POSTED if result["applied"] else Status.EXCEPTION
    audit.append("remittance", remittance.id, "cash_application_completed", "agent:ar-matcher", result)
    return {"remittance": remittance.to_dict(), "result": result}


@app.get("/api/v1/audit-log", tags=["Audit"])
def audit_log(entity_id: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    return {"chain_valid": audit.verify(), "events": audit.list(entity_id, limit)}


@app.get("/api/v1/health", tags=["Operations"])
def health() -> dict[str, Any]:
    return {"status": "ok", "version": "0.1.0", "environment": settings.app_env, "database": repo.backend, "erp": settings.erp_mode, "audit_chain_valid": audit.verify()}


@app.post("/api/v1/mailbox/poll", tags=["Ingestion"])
def poll_mailbox(max_messages: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
    from app.email_ingestion import GraphMailboxAdapter

    try:
        messages = GraphMailboxAdapter.from_settings(settings).fetch_unread(max_messages)
        accepted = []
        for message in messages:
            for attachment in message.attachments:
                try:
                    invoice = extract_invoice(attachment.content, attachment.filename, f"graph:{message.message_id}:{attachment.filename}")
                    accepted.append(workflow.ingest(invoice)["invoice"]["id"])
                except ValueError as exc:
                    audit.append("email", message.message_id, "attachment_rejected", "agent:ingestor", {"filename": attachment.filename, "reason": str(exc)})
        return {"messages_scanned": len(messages), "invoices_accepted": accepted}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/review", response_class=HTMLResponse, include_in_schema=False)
def review_ui() -> str:
    rows = "".join(f"<tr><td>{i.id}</td><td>{i.vendor_id}</td><td>{i.invoice_number}</td><td>{i.total} {i.currency}</td><td>{i.status.value}</td></tr>" for i in repo.invoices.values() if i.status == Status.EXCEPTION)
    return f"""<!doctype html><html><head><title>LedgerPilot Review</title><style>body{{font:16px system-ui;margin:3rem;color:#172033}}h1{{color:#155e75}}table{{border-collapse:collapse;width:100%}}th,td{{padding:.7rem;border-bottom:1px solid #dbe4ea;text-align:left}}th{{background:#ecfeff}}</style></head><body><h1>Exception review queue</h1><p>Use <code>POST /api/v1/exceptions/decision</code> or Swagger to approve or reject.</p><table><thead><tr><th>ID</th><th>Vendor</th><th>Invoice</th><th>Total</th><th>Status</th></tr></thead><tbody>{rows or '<tr><td colspan="5">No exceptions</td></tr>'}</tbody></table></body></html>"""
