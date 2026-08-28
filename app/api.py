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
    if repo.get_invoice(request.invoice_id) is None:
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
    if repo.get_invoice(request.invoice_id) is None:
        raise HTTPException(404, "invoice not found")
    return workflow.approve(request.invoice_id, request.actor, request.approved, request.comment)


@app.get("/api/v1/exceptions", tags=["Human oversight"])
def list_exceptions() -> list[dict[str, Any]]:
    response = []
    for invoice in repo.list_invoices(Status.EXCEPTION):
        latest_match = repo.latest_match(invoice.id)
        response.append(
            {
                **invoice.to_dict(),
                "match": latest_match.to_dict() if latest_match else None,
            }
        )
    return response


@app.get("/api/v1/workflows/{invoice_id}", tags=["Operations"])
def workflow_status(invoice_id: str) -> dict[str, Any]:
    if repo.get_invoice(invoice_id) is None:
        raise HTTPException(404, "invoice not found")
    return repo.get_workflow_state(invoice_id) or {"node": "unknown", "state": {"invoice_id": invoice_id}}


@app.post("/api/v1/ingest-remittance", status_code=202, tags=["AR"])
def ingest_remittance(request: RemittanceRequest) -> dict[str, Any]:
    remittance = extract_remittance(request.model_dump(), request.source_ref)
    result = erp.apply_cash(remittance.customer_id, remittance.amount, remittance.open_item_refs)
    remittance.status = Status.POSTED if result["applied"] else Status.EXCEPTION
    repo.save_remittance(remittance, result)
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
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LedgerPilot · Exception Review</title>
<style>
:root{--ink:#172033;--muted:#667085;--teal:#0e7490;--teal2:#155e75;--line:#dbe4ea;--paper:#fff;--bg:#f3f7f9;--danger:#b42318}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 Inter,ui-sans-serif,system-ui;color:var(--ink);background:var(--bg)}
header{padding:28px max(24px,5vw);color:#fff;background:linear-gradient(120deg,#083344,#155e75)}
header h1{margin:0;font-size:26px}header p{margin:5px 0 0;color:#cffafe}.wrap{max-width:1180px;margin:28px auto;padding:0 24px}
.stats{display:flex;gap:12px;margin-bottom:18px}.pill{background:#e6fffb;border:1px solid #99f6e4;border-radius:999px;padding:7px 13px;color:#115e59;font-weight:700}
.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;box-shadow:0 10px 30px #0f172a0b;overflow:hidden}
table{border-collapse:collapse;width:100%}th,td{padding:14px 13px;border-bottom:1px solid #edf2f5;text-align:left;vertical-align:top}th{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);background:#f8fafc}
.code{font:12px ui-monospace,SFMono-Regular,monospace}.variance{color:var(--danger);font-weight:650}.empty{padding:56px;text-align:center;color:var(--muted)}
input{width:170px;padding:8px;border:1px solid #cbd5e1;border-radius:7px}button{border:0;border-radius:7px;padding:8px 11px;color:#fff;cursor:pointer;font-weight:700;margin:2px}.approve{background:var(--teal)}.reject{background:#b42318}button:disabled{opacity:.5}
#toast{position:fixed;right:22px;bottom:22px;background:#172033;color:white;padding:12px 16px;border-radius:9px;display:none}
</style></head><body>
<header><h1>LedgerPilot exception desk</h1><p>Evidence-led review with explicit actor, comment, and immutable audit event.</p></header>
<main class="wrap"><div class="stats"><span class="pill" id="count">Loading queue…</span><span class="pill">Deterministic controls remain authoritative</span></div>
<section class="card"><div id="queue" class="empty">Loading exceptions…</div></section></main><div id="toast"></div>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){const r=await fetch('/api/v1/exceptions');const xs=await r.json();document.querySelector('#count').textContent=`${xs.length} exception${xs.length===1?'':'s'} awaiting review`;
if(!xs.length){document.querySelector('#queue').innerHTML='<div class="empty">No exceptions. The queue is clear.</div>';return}
document.querySelector('#queue').innerHTML=`<table><thead><tr><th>Invoice</th><th>Supplier / amount</th><th>Control evidence</th><th>Reviewer action</th></tr></thead><tbody>${xs.map(x=>{const vs=x.match?.variances||[];return `<tr><td><b>${esc(x.invoice_number)}</b><br><span class="code">${esc(x.id)}</span><br>PO ${esc(x.po_number||'—')}</td><td>${esc(x.vendor_id)}<br><b>${esc(x.total)} ${esc(x.currency)}</b><br>Confidence ${Math.round((x.confidence||0)*100)}%</td><td>${vs.map(v=>`<div class="variance">${esc(v.code)}</div><div>${esc(v.message)}</div>`).join('')||'No variance detail'}</td><td><input id="c-${x.id}" placeholder="Required comment"><br><button class="approve" onclick="decide('${x.id}',true)">Approve</button><button class="reject" onclick="decide('${x.id}',false)">Reject</button></td></tr>`}).join('')}</tbody></table>`}
async function decide(id,approved){const comment=document.querySelector(`#c-${id}`).value.trim();if(comment.length<3){toast('Add a reviewer comment first');return}const r=await fetch('/api/v1/exceptions/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({invoice_id:id,approved,actor:'reviewer:web',comment})});if(!r.ok){toast((await r.json()).detail||'Decision failed');return}toast(approved?'Approved and audited':'Rejected and audited');await load()}
function toast(message){const el=document.querySelector('#toast');el.textContent=message;el.style.display='block';setTimeout(()=>el.style.display='none',2600)}load();
</script></body></html>"""
