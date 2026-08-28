from __future__ import annotations

from typing import Any, TypedDict

from app.audit import AuditLedger
from app.config import Settings
from app.context import ContextRetriever
from app.domain import Invoice, Status
from app.erp import MockERP
from app.matching import match_invoice
from app.llm import DecisionExplainer
from app.repository import MemoryRepository


class WorkflowState(TypedDict, total=False):
    invoice_id: str
    result: dict[str, Any]
    next_action: str


class InvoiceWorkflow:
    def __init__(self, repo: MemoryRepository, audit: AuditLedger, erp: MockERP, config: Settings) -> None:
        self.repo, self.audit, self.erp, self.config = repo, audit, erp, config
        self.context = ContextRetriever()
        self.explainer = DecisionExplainer(config)
        self.graph = self._build_graph()

    def _build_graph(self):
        try:
            from langgraph.graph import END, START, StateGraph

            graph = StateGraph(WorkflowState)
            graph.add_node("match", self._graph_match)
            graph.add_node("route", self._graph_route)
            graph.add_edge(START, "match")
            graph.add_edge("match", "route")
            graph.add_edge("route", END)
            return graph.compile()
        except ImportError:
            return None

    def _graph_match(self, state: WorkflowState) -> WorkflowState:
        return {**state, **self.match(state["invoice_id"])}

    def _graph_route(self, state: WorkflowState) -> WorkflowState:
        return state

    def ingest(self, invoice: Invoice) -> dict[str, Any]:
        invoice.status = Status.EXTRACTED
        self.repo.save_invoice(invoice)
        self.audit.append("invoice", invoice.id, "invoice_extracted", "agent:extractor", {"source_ref": invoice.source_ref, "confidence": invoice.confidence, "evidence": invoice.evidence})
        return {"invoice": invoice.to_dict(), "next_action": "match-po"}

    def match(self, invoice_id: str, require_goods_receipt: bool = True) -> dict[str, Any]:
        invoice = self.repo.invoices[invoice_id]
        po = self.erp.get_purchase_order(invoice.po_number)
        duplicate = self.repo.find_duplicate(invoice.vendor_id, invoice.invoice_number, exclude_id=invoice.id) is not None
        result = match_invoice(invoice, po, self.config.price_tolerance_pct, self.config.quantity_tolerance_pct, self.config.total_tolerance_pct, require_goods_receipt, duplicate)
        self.repo.save_match(result)
        invoice.status = Status.MATCHED if result.matched else Status.EXCEPTION
        self.repo.save_invoice(invoice)
        policies = self.context.retrieve("invoice matching duplicate PO goods receipt")
        explanation = self.explainer.explain(result.to_dict())
        self.audit.append("invoice", invoice.id, "match_completed", "agent:matcher", {"match_id": result.id, "matched": result.matched, "variances": [v.to_dict() for v in result.variances], "policies": policies, "explanation": explanation})
        if result.matched and self.config.auto_post_enabled and not self.config.require_human_approval:
            journal = self.post(invoice.id, f"auto:{invoice.id}")
            return {"result": result.to_dict(), "next_action": "posted", "journal": journal}
        next_action = "post-payment-journal" if result.matched and not self.config.require_human_approval else "human-review"
        return {"result": result.to_dict(), "next_action": next_action}

    def approve(self, invoice_id: str, actor: str, approved: bool, comment: str) -> dict[str, Any]:
        invoice = self.repo.invoices[invoice_id]
        invoice.status = Status.APPROVED if approved else Status.REJECTED
        self.repo.save_invoice(invoice)
        self.audit.append("invoice", invoice.id, "human_approved" if approved else "human_rejected", actor, {"comment": comment})
        return {"invoice_id": invoice.id, "status": invoice.status.value}

    def post(self, invoice_id: str, idempotency_key: str, actor: str = "agent:poster") -> dict[str, Any]:
        invoice = self.repo.invoices[invoice_id]
        match = next((m for m in reversed(list(self.repo.matches.values())) if m.invoice_id == invoice_id), None)
        if match is None or (not match.matched and invoice.status != Status.APPROVED):
            raise ValueError("invoice does not have a successful match")
        if self.config.require_human_approval and invoice.status != Status.APPROVED:
            raise ValueError("human approval is required before posting")
        journal = self.erp.post_payment_journal(invoice, idempotency_key)
        invoice.status = Status.POSTED
        self.repo.save_invoice(invoice)
        self.repo.journals[journal["journal_id"]] = journal
        self.audit.append("invoice", invoice.id, "payment_journal_posted", actor, journal)
        return journal
