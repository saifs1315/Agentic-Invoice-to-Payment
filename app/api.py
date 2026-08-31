from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.agent_runtime import AIRuntimeUnavailableError, AgentProtocolError
from app.bootstrap import (
    agent_runtime,
    ar_workflow,
    audit,
    orchestrator,
    repo,
    runtime_capabilities,
    workflow,
)
from app.config import settings
from app.domain import DocumentKind, SourceEnvelope, Status
from app.extraction import extract_remittance
from app.erp import ERPConflictError, ERPUnavailableError

app = FastAPI(
    title="LedgerPilot API",
    version="0.3.0",
    description="Auditable mandatory-AI invoice-to-payment and remittance automation",
)
ERP_API_RESPONSES = {
    409: {"description": "ERP business-state conflict"},
    503: {"description": "ERP transport or server failure"},
}


@app.exception_handler(ERPConflictError)
async def erp_conflict_handler(_: Request, exc: ERPConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ERPUnavailableError)
async def erp_unavailable_handler(_: Request, exc: ERPUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(AIRuntimeUnavailableError)
async def ai_unavailable_handler(_: Request, exc: AIRuntimeUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc), "ai_required": True})


@app.exception_handler(AgentProtocolError)
async def agent_protocol_handler(_: Request, exc: AgentProtocolError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": str(exc), "ai_required": True})


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
    amount: Decimal = Field(gt=0, le=settings.max_monetary_amount)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    open_item_refs: list[str]
    source_ref: str = "api"


class ARCorrections(BaseModel):
    customer_id: str | None = None
    reference: str | None = None
    amount: Decimal | None = Field(default=None, gt=0, le=settings.max_monetary_amount)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    open_item_refs: list[str] | None = None


class ARDecisionRequest(BaseModel):
    remittance_id: str
    action: str = Field(
        pattern=r"^(RETRY_WITH_CORRECTION|REJECT|MARK_MANUALLY_RESOLVED|APPROVE_APPLY)$"
    )
    actor: str = Field(min_length=3)
    comment: str = Field(min_length=3)
    corrections: ARCorrections | None = None


def _source_envelope(
    content: bytes,
    filename: str,
    media_type: str,
    source_ref: str,
    workflow_hint: DocumentKind | None = None,
    message_id: str | None = None,
) -> SourceEnvelope:
    return SourceEnvelope(
        content=content,
        filename=filename,
        media_type=media_type,
        source_ref=source_ref,
        content_sha256=hashlib.sha256(content).hexdigest(),
        workflow_hint=workflow_hint,
        message_id=message_id,
    )


async def _read_upload_limited(file: UploadFile) -> bytes:
    """Read at most the configured limit plus one byte from the spooled upload."""
    limit = settings.max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "attachment exceeds configured maximum")
        chunks.append(chunk)


@app.post("/api/v1/ingest-invoice", status_code=202, tags=["AP"])
async def ingest_invoice(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await _read_upload_limited(file)
    source_hash = hashlib.sha256(content).hexdigest()
    source_ref = f"sha256:{source_hash}"
    try:
        envelope = _source_envelope(
            content,
            file.filename or "invoice.bin",
            file.content_type or "application/octet-stream",
            source_ref,
            DocumentKind.AP_INVOICE,
        )
        return orchestrator.ingest_only(envelope, DocumentKind.AP_INVOICE)
    except (ValueError, json.JSONDecodeError) as exc:
        audit.append(
            "source_document",
            source_ref,
            "extraction_failed",
            "agent:extractor",
            {"filename": file.filename or "invoice.bin", "reason": str(exc)},
        )
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/v1/match-po", tags=["AP"], responses=ERP_API_RESPONSES)
def match_po(request: MatchRequest) -> dict[str, Any]:
    if repo.get_invoice(request.invoice_id) is None:
        raise HTTPException(404, "invoice not found")
    try:
        return workflow.match(request.invoice_id, request.require_goods_receipt)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post(
    "/api/v1/post-payment-journal",
    tags=["AP"],
    responses=ERP_API_RESPONSES,
)
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
    try:
        return workflow.approve(request.invoice_id, request.actor, request.approved, request.comment)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/v1/exceptions", tags=["Human oversight"])
def list_exceptions() -> list[dict[str, Any]]:
    response = []
    reviewable = repo.list_invoices(Status.EXCEPTION) + repo.list_invoices(
        Status.AWAITING_APPROVAL
    )
    for invoice in reviewable:
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
    state = repo.get_finance_workflow_state(invoice_id) or repo.get_workflow_state(invoice_id)
    if state is None:
        raise HTTPException(404, "workflow not found")
    return state


@app.post(
    "/api/v1/ingest-document",
    status_code=202,
    tags=["Ingestion"],
    responses=ERP_API_RESPONSES,
)
async def ingest_document(
    file: UploadFile = File(...),
    workflow_hint: DocumentKind | None = Query(default=None),
) -> dict[str, Any]:
    content = await _read_upload_limited(file)
    source_hash = hashlib.sha256(content).hexdigest()
    source_ref = f"sha256:{source_hash}"
    envelope = _source_envelope(
        content,
        file.filename or "financial-document.bin",
        file.content_type or "application/octet-stream",
        source_ref,
        workflow_hint,
    )
    try:
        return orchestrator.ingest(envelope)
    except (ValueError, json.JSONDecodeError) as exc:
        audit.append(
            "source_document",
            source_ref,
            "extraction_failed",
            "agent:finance-orchestrator",
            {"filename": file.filename or "financial-document.bin", "reason": str(exc)},
        )
        raise HTTPException(422, str(exc)) from exc


@app.post(
    "/api/v1/ingest-remittance",
    status_code=202,
    tags=["AR"],
    responses=ERP_API_RESPONSES,
)
def ingest_remittance(request: RemittanceRequest) -> dict[str, Any]:
    remittance = extract_remittance(request.model_dump(), request.source_ref)
    return ar_workflow.ingest(remittance, run=True)


@app.get("/api/v1/remittance-exceptions", tags=["AR", "Human oversight"])
def list_remittance_exceptions() -> list[dict[str, Any]]:
    return repo.list_remittances(Status.EXCEPTION)


@app.post(
    "/api/v1/remittance-exceptions/decision",
    tags=["AR", "Human oversight"],
    responses=ERP_API_RESPONSES,
)
def remittance_exception_decision(request: ARDecisionRequest) -> dict[str, Any]:
    if repo.get_remittance(request.remittance_id) is None:
        raise HTTPException(404, "remittance not found")
    try:
        return ar_workflow.decide(
            request.remittance_id,
            request.action,
            request.actor,
            request.comment,
            request.corrections.model_dump(exclude_none=True) if request.corrections else None,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/v1/finance-exceptions", tags=["Human oversight"])
def list_finance_exceptions(workflow_type: str | None = Query(default=None)) -> list[dict[str, Any]]:
    response: list[dict[str, Any]] = []
    if workflow_type in {None, "ap"}:
        for invoice in repo.list_invoices(Status.EXCEPTION) + repo.list_invoices(
            Status.AWAITING_APPROVAL
        ):
            latest_match = repo.latest_match(invoice.id)
            response.append(
                {
                    "workflow_type": "ap",
                    "entity": invoice.to_dict(),
                    "result": latest_match.to_dict() if latest_match else {},
                }
            )
    if workflow_type in {None, "ar"}:
        response.extend(
            {"workflow_type": "ar", "entity": item["remittance"], "result": item["result"]}
            for status in (Status.EXCEPTION, Status.AWAITING_APPROVAL)
            for item in repo.list_remittances(status)
        )
    return response


@app.get("/api/v1/audit-log", tags=["Audit"])
def audit_log(entity_id: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    return {"chain_valid": audit.verify(), "events": audit.list(entity_id, limit)}


@app.get("/api/v1/health", tags=["Operations"])
def health(response: Response) -> dict[str, Any]:
    ai = agent_runtime.capabilities()
    capabilities = {
        **runtime_capabilities,
        "agent_runtime": ai,
        "supervisor_agent_ready": ai["ready"],
        "ap_agent_ready": ai["ready"],
        "ar_agent_ready": ai["ready"],
    }
    degraded = bool(runtime_capabilities["repository_degraded"])
    if not ai["ready"]:
        response.status_code = 503
    return {
        "status": "unavailable" if not ai["ready"] else ("degraded" if degraded else "ok"),
        "version": "0.3.0",
        "environment": settings.app_env,
        "database": repo.backend,
        "erp": settings.erp_mode,
        "erp_base_url": settings.erp_base_url if settings.erp_mode == "http" else None,
        "audit_chain_valid": audit.integrity_status(),
        "capabilities": capabilities,
    }


@app.post("/api/v1/mailbox/poll", tags=["Ingestion"])
def poll_mailbox(max_messages: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
    from app.email_ingestion import GraphMailboxAdapter

    try:
        messages = GraphMailboxAdapter.from_settings(settings).fetch_unread(max_messages)
        accepted = []
        for message in messages:
            for attachment in message.attachments:
                try:
                    source_ref = f"graph:{message.message_id}:{attachment.filename}"
                    response = orchestrator.ingest(
                        _source_envelope(
                            attachment.content,
                            attachment.filename,
                            attachment.content_type,
                            source_ref,
                            message_id=message.message_id,
                        )
                    )
                    accepted.append(
                        {
                            "entity_id": response["entity_id"],
                            "workflow_type": response["workflow_type"],
                        }
                    )
                except ValueError as exc:
                    audit.append("email", message.message_id, "attachment_rejected", "agent:ingestor", {"filename": attachment.filename, "reason": str(exc)})
        return {"messages_scanned": len(messages), "documents_accepted": accepted}
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
input,textarea{width:210px;padding:8px;border:1px solid #cbd5e1;border-radius:7px;margin-bottom:5px}textarea{height:58px;font:12px ui-monospace,SFMono-Regular,monospace}button{border:0;border-radius:7px;padding:8px 11px;color:#fff;cursor:pointer;font-weight:700;margin:2px}.approve{background:var(--teal)}.reject{background:#b42318}.manual{background:#475467}button:disabled{opacity:.5}
#toast{position:fixed;right:22px;bottom:22px;background:#172033;color:white;padding:12px 16px;border-radius:9px;display:none}
</style></head><body>
<header><h1>LedgerPilot exception desk</h1><p>Evidence-led review with explicit actor, comment, and immutable audit event.</p></header>
<main class="wrap"><div class="stats"><span class="pill" id="count">Loading queue…</span><span class="pill">Deterministic controls remain authoritative</span></div>
<section class="card"><div id="queue" class="empty">Loading exceptions…</div></section></main><div id="toast"></div>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){const r=await fetch('/api/v1/finance-exceptions');const xs=await r.json();document.querySelector('#count').textContent=`${xs.length} exception${xs.length===1?'':'s'} awaiting review`;
if(!xs.length){document.querySelector('#queue').innerHTML='<div class="empty">No exceptions. The queue is clear.</div>';return}
document.querySelector('#queue').innerHTML=`<table><thead><tr><th>Workflow / entity</th><th>Counterparty / amount</th><th>Control evidence</th><th>Reviewer action</th></tr></thead><tbody>${xs.map(x=>{const e=x.entity,vs=x.result?.variances||[];const ap=x.workflow_type==='ap';return `<tr><td><b>${ap?'AP invoice':'AR remittance'}</b><br>${esc(ap?e.invoice_number:e.reference)}<br><span class="code">${esc(e.id)}</span><br>${ap?'PO '+esc(e.po_number||'—'):'Items '+esc((e.open_item_refs||[]).join(', '))}</td><td>${esc(ap?e.vendor_id:e.customer_id)}<br><b>${esc(ap?e.total:e.amount)} ${esc(e.currency)}</b><br>Confidence ${Math.round((e.confidence||0)*100)}%</td><td>${vs.map(v=>`<div class="variance">${esc(v.code)}</div><div>${esc(v.message)}</div>`).join('')||'No variance detail'}</td><td><input id="c-${e.id}" placeholder="Required comment"><br>${ap?`<button class="approve" onclick="decideAP('${e.id}',true)">Approve</button><button class="reject" onclick="decideAP('${e.id}',false)">Reject</button>`:`<textarea id="x-${e.id}" placeholder='Optional correction JSON, e.g. {"amount":"1000.00"}'></textarea><br><button class="approve" onclick="decideAR('${e.id}','RETRY_WITH_CORRECTION')">Correct & retry</button><button class="reject" onclick="decideAR('${e.id}','REJECT')">Reject</button><button class="manual" onclick="decideAR('${e.id}','MARK_MANUALLY_RESOLVED')">Manual resolve</button>`}</td></tr>`}).join('')}</tbody></table>`}
function comment(id){const value=document.querySelector(`#c-${id}`).value.trim();if(value.length<3){toast('Add a reviewer comment first');return null}return value}
async function decideAP(id,approved){const note=comment(id);if(!note)return;const r=await fetch('/api/v1/exceptions/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({invoice_id:id,approved,actor:'reviewer:web',comment:note})});if(!r.ok){toast((await r.json()).detail||'Decision failed');return}toast('AP decision audited');await load()}
async function decideAR(id,action){const note=comment(id);if(!note)return;let corrections=null;if(action==='RETRY_WITH_CORRECTION'){try{corrections=JSON.parse(document.querySelector(`#x-${id}`).value)}catch{toast('Enter valid correction JSON');return}}const r=await fetch('/api/v1/remittance-exceptions/decision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({remittance_id:id,action,actor:'reviewer:web',comment:note,corrections})});if(!r.ok){toast((await r.json()).detail||'Decision failed');return}toast('AR decision audited');await load()}
function toast(message){const el=document.querySelector('#toast');el.textContent=message;el.style.display='block';setTimeout(()=>el.style.display='none',2600)}load();
</script></body></html>"""
