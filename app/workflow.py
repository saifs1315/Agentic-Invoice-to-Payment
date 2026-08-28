from __future__ import annotations

from typing import Any, TypedDict

from app.audit import AuditLedger
from app.config import Settings
from app.context import ContextRetriever
from app.domain import Invoice, Status
from app.erp import MockERP
from app.llm import DecisionExplainer
from app.matching import match_invoice
from app.repository import MemoryRepository


class WorkflowState(TypedDict, total=False):
    invoice_id: str
    require_goods_receipt: bool
    policies: list[str]
    result: dict[str, Any]
    explanation: str
    next_action: str
    journal: dict[str, Any]


class InvoiceWorkflow:
    """LangGraph-orchestrated AP flow with deterministic financial controls."""

    def __init__(self, repo: MemoryRepository, audit: AuditLedger, erp: MockERP, config: Settings) -> None:
        self.repo, self.audit, self.erp, self.config = repo, audit, erp, config
        self.context = ContextRetriever(repo)
        self.explainer = DecisionExplainer(config)
        self.graph = self._build_graph()

    def _build_graph(self):
        try:
            from langgraph.graph import END, START, StateGraph

            graph = StateGraph(WorkflowState)
            graph.add_node("retrieve_policy", self._graph_retrieve_policy)
            graph.add_node("deterministic_match", self._graph_match)
            graph.add_node("auto_post", self._graph_post)
            graph.add_node("review_or_ready", self._graph_finalize)
            graph.add_edge(START, "retrieve_policy")
            graph.add_edge("retrieve_policy", "deterministic_match")
            graph.add_conditional_edges(
                "deterministic_match",
                self._route_after_match,
                {"post": "auto_post", "review": "review_or_ready"},
            )
            graph.add_edge("auto_post", END)
            graph.add_edge("review_or_ready", END)
            return graph.compile()
        except ImportError:
            return None

    def _persist(self, node: str, state: WorkflowState) -> None:
        self.repo.save_workflow_state(state["invoice_id"], node, dict(state))

    def _graph_retrieve_policy(self, state: WorkflowState) -> WorkflowState:
        update: WorkflowState = {
            "policies": self.context.retrieve("invoice matching duplicate PO goods receipt posting approval")
        }
        self._persist("retrieve_policy", {**state, **update})
        return update

    def _graph_match(self, state: WorkflowState) -> WorkflowState:
        result, explanation = self._match_core(
            state["invoice_id"],
            state.get("require_goods_receipt", True),
            state.get("policies", []),
        )
        update: WorkflowState = {"result": result, "explanation": explanation}
        self._persist("deterministic_match", {**state, **update})
        return update

    def _route_after_match(self, state: WorkflowState) -> str:
        matched = bool(state["result"]["matched"])
        if matched and self.config.auto_post_enabled and not self.config.require_human_approval:
            return "post"
        return "review"

    def _graph_post(self, state: WorkflowState) -> WorkflowState:
        journal = self.post(state["invoice_id"], f"auto:{state['invoice_id']}")
        update: WorkflowState = {"next_action": "posted", "journal": journal}
        self._persist("auto_post", {**state, **update})
        return update

    def _graph_finalize(self, state: WorkflowState) -> WorkflowState:
        matched = bool(state["result"]["matched"])
        next_action = (
            "post-payment-journal"
            if matched and not self.config.require_human_approval
            else "human-review"
        )
        update: WorkflowState = {"next_action": next_action}
        self._persist("review_or_ready", {**state, **update})
        return update

    def _invoice_or_raise(self, invoice_id: str) -> Invoice:
        invoice = self.repo.get_invoice(invoice_id)
        if invoice is None:
            raise KeyError(invoice_id)
        return invoice

    def ingest(self, invoice: Invoice) -> dict[str, Any]:
        invoice.status = Status.EXTRACTED
        self.repo.save_invoice(invoice)
        self.audit.append(
            "invoice",
            invoice.id,
            "invoice_extracted",
            "agent:extractor",
            {
                "source_ref": invoice.source_ref,
                "confidence": invoice.confidence,
                "evidence": invoice.evidence,
            },
        )
        state: WorkflowState = {"invoice_id": invoice.id, "next_action": "match-po"}
        self._persist("ingested", state)
        return {"invoice": invoice.to_dict(), "next_action": "match-po"}

    def _match_core(
        self,
        invoice_id: str,
        require_goods_receipt: bool,
        policies: list[str],
    ) -> tuple[dict[str, Any], str]:
        invoice = self._invoice_or_raise(invoice_id)
        po = self.erp.get_purchase_order(invoice.po_number)
        duplicate = (
            self.repo.find_duplicate(
                invoice.vendor_id,
                invoice.invoice_number,
                exclude_id=invoice.id,
            )
            is not None
        )
        result = match_invoice(
            invoice,
            po,
            self.config.price_tolerance_pct,
            self.config.quantity_tolerance_pct,
            self.config.total_tolerance_pct,
            require_goods_receipt,
            duplicate,
        )
        self.repo.save_match(result)
        invoice.status = Status.MATCHED if result.matched else Status.EXCEPTION
        self.repo.save_invoice(invoice)
        explanation = self.explainer.explain(result.to_dict())
        self.audit.append(
            "invoice",
            invoice.id,
            "match_completed",
            "agent:matcher",
            {
                "match_id": result.id,
                "matched": result.matched,
                "variances": [variance.to_dict() for variance in result.variances],
                "policies": policies,
                "explanation": explanation,
            },
        )
        return result.to_dict(), explanation

    def match(self, invoice_id: str, require_goods_receipt: bool = True) -> dict[str, Any]:
        self._invoice_or_raise(invoice_id)
        initial: WorkflowState = {
            "invoice_id": invoice_id,
            "require_goods_receipt": require_goods_receipt,
        }
        if self.graph is not None:
            state = self.graph.invoke(initial)
        else:
            policies = self.context.retrieve("invoice matching duplicate PO goods receipt posting approval")
            result, explanation = self._match_core(invoice_id, require_goods_receipt, policies)
            state = {**initial, "policies": policies, "result": result, "explanation": explanation}
            if self._route_after_match(state) == "post":
                state.update(self._graph_post(state))
            else:
                state.update(self._graph_finalize(state))
        return {
            key: state[key]
            for key in ("result", "next_action", "journal", "policies", "explanation")
            if key in state
        }

    def approve(self, invoice_id: str, actor: str, approved: bool, comment: str) -> dict[str, Any]:
        invoice = self._invoice_or_raise(invoice_id)
        invoice.status = Status.APPROVED if approved else Status.REJECTED
        self.repo.save_invoice(invoice)
        self.audit.append(
            "invoice",
            invoice.id,
            "human_approved" if approved else "human_rejected",
            actor,
            {"comment": comment},
        )
        self._persist(
            "human_approved" if approved else "human_rejected",
            {"invoice_id": invoice.id, "next_action": "post-payment-journal" if approved else "closed"},
        )
        return {"invoice_id": invoice.id, "status": invoice.status.value}

    def post(self, invoice_id: str, idempotency_key: str, actor: str = "agent:poster") -> dict[str, Any]:
        invoice = self._invoice_or_raise(invoice_id)
        existing = self.repo.get_journal(invoice_id, idempotency_key)
        if existing:
            return existing
        match = self.repo.latest_match(invoice_id)
        if match is None or (not match.matched and invoice.status != Status.APPROVED):
            raise ValueError("invoice does not have a successful match or explicit exception approval")
        if self.config.require_human_approval and invoice.status != Status.APPROVED:
            raise ValueError("human approval is required before posting")
        journal = self.erp.post_payment_journal(invoice, idempotency_key)
        invoice.status = Status.POSTED
        self.repo.save_invoice(invoice)
        self.repo.save_journal(journal)
        self.audit.append("invoice", invoice.id, "payment_journal_posted", actor, journal)
        return journal
