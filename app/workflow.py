from __future__ import annotations

from typing import Any, TypedDict

from app.agent_runtime import AgentProtocolError, AgentRuntime, create_agent_runtime
from app.audit import AuditLedger
from app.config import Settings
from app.context import ContextRetriever
from app.domain import Invoice, Status
from app.erp import ERPClient, ERPError
from app.matching import match_invoice
from app.repository import MemoryRepository


class WorkflowState(TypedDict, total=False):
    invoice_id: str
    require_goods_receipt: bool
    policies: list[str]
    policy_ids: list[str]
    result: dict[str, Any]
    explanation: str
    next_action: str
    journal: dict[str, Any]
    error: dict[str, Any]
    agent_decisions: list[dict[str, Any]]


class InvoiceWorkflow:
    """LangGraph-orchestrated AP flow with deterministic financial controls."""

    def __init__(
        self,
        repo: MemoryRepository,
        audit: AuditLedger,
        erp: ERPClient,
        config: Settings,
        runtime: AgentRuntime | None = None,
    ) -> None:
        self.repo, self.audit, self.erp, self.config = repo, audit, erp, config
        self.runtime = runtime or create_agent_runtime(config)
        self.context = ContextRetriever(repo, self.runtime, config)
        self.graph = self._build_graph()

    def _agent_decide(
        self,
        invoice_id: str,
        stage: str,
        evidence: dict[str, Any],
        allowed_actions: list[str],
    ) -> dict[str, Any]:
        decision = self.runtime.decide(
            domain="ap",
            stage=stage,
            evidence=evidence,
            allowed_actions=allowed_actions,
        ).model_dump()
        self.audit.append(
            "invoice",
            invoice_id,
            "agent_decision",
            "agent:ap",
            {"stage": stage, "allowed_actions": allowed_actions, **decision},
        )
        return decision

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
        invoice = self.repo.get_invoice(state["invoice_id"])
        self.repo.save_finance_workflow_state(
            state["invoice_id"],
            "ap",
            node,
            invoice.status.value if invoice else "unknown",
            dict(state),
            invoice.source_ref if invoice else None,
        )

    def _graph_retrieve_policy(self, state: WorkflowState) -> WorkflowState:
        query_seed = "invoice matching duplicate PO goods receipt posting approval"
        decision = self._agent_decide(
            state["invoice_id"],
            "retrieve_policy",
            {"query_seed": query_seed},
            ["RETRIEVE_POLICY"],
        )
        try:
            policies_with_ids, query, fallback_used = self.context.retrieve_agent_query(
                decision.get("retrieval_query"),
                query_seed,
            )
        except AgentProtocolError as exc:
            self.audit.append(
                "invoice",
                state["invoice_id"],
                "policy_retrieval_blocked",
                "control:policy-evidence",
                {"agent_query": decision.get("retrieval_query"), "reason": str(exc)},
            )
            raise
        if fallback_used:
            self.audit.append(
                "invoice",
                state["invoice_id"],
                "policy_retrieval_fallback",
                "control:policy-evidence",
                {"agent_query": decision.get("retrieval_query"), "effective_query": query},
            )
        update: WorkflowState = {
            "policies": [policy for _, policy in policies_with_ids],
            "policy_ids": [policy_id for policy_id, _ in policies_with_ids],
            "agent_decisions": [*state.get("agent_decisions", []), decision],
        }
        self._persist("retrieve_policy", {**state, **update})
        return update

    def _graph_match(self, state: WorkflowState) -> WorkflowState:
        match_decision = self._agent_decide(
            state["invoice_id"],
            "request_erp_match",
            {
                "expected_action": "RUN_AP_MATCH",
                "policies": state.get("policies", []),
                "policy_ids": state.get("policy_ids", []),
                "require_goods_receipt": state.get("require_goods_receipt", True),
            },
            ["RUN_AP_MATCH"],
        )
        result, explanation = self._match_core(
            state["invoice_id"],
            state.get("require_goods_receipt", True),
            state.get("policies", []),
        )
        auto_action_permitted = bool(
            result["matched"]
            and self.config.auto_post_enabled
            and not self.config.require_human_approval
        )
        decision = self._agent_decide(
            state["invoice_id"],
            "evaluate_match",
            {
                "deterministic_result": result,
                "policies": state.get("policies", []),
                "policy_ids": state.get("policy_ids", []),
                "control_eligibility": {
                    "auto_action_permitted": auto_action_permitted,
                    "auto_post_enabled": self.config.auto_post_enabled,
                    "human_approval_required": self.config.require_human_approval,
                },
            },
            ["POST_PAYMENT_JOURNAL", "ESCALATE"],
        )
        requested_action = decision["action"]
        decision["requested_action"] = requested_action
        decision["guard_outcome"] = "accepted"
        if requested_action == "POST_PAYMENT_JOURNAL" and not auto_action_permitted:
            decision["action"] = "ESCALATE"
            decision["guard_outcome"] = "vetoed_by_deterministic_controls"
            self.audit.append(
                "invoice",
                state["invoice_id"],
                "agent_action_vetoed",
                "control:ap-posting-guard",
                {
                    "requested_action": requested_action,
                    "effective_action": "ESCALATE",
                    "matched": result["matched"],
                    "auto_post_enabled": self.config.auto_post_enabled,
                    "human_approval_required": self.config.require_human_approval,
                },
            )
        update: WorkflowState = {
            "result": result,
            "explanation": explanation,
            "agent_decisions": [
                *state.get("agent_decisions", []),
                match_decision,
                decision,
            ],
        }
        self._persist("deterministic_match", {**state, **update})
        return update

    def _route_after_match(self, state: WorkflowState) -> str:
        matched = bool(state["result"]["matched"])
        action = state.get("agent_decisions", [{}])[-1].get("action")
        if (
            matched
            and action == "POST_PAYMENT_JOURNAL"
            and self.config.auto_post_enabled
            and not self.config.require_human_approval
        ):
            return "post"
        return "review"

    def _graph_post(self, state: WorkflowState) -> WorkflowState:
        journal = self.post(
            state["invoice_id"],
            f"auto:{state['invoice_id']}",
        )
        update: WorkflowState = {"next_action": "posted", "journal": journal}
        self._persist("auto_post", {**state, **update})
        return update

    def _graph_finalize(self, state: WorkflowState) -> WorkflowState:
        matched = bool(state["result"]["matched"])
        agent_escalated = (
            self.config.auto_post_enabled
            and state.get("agent_decisions", [{}])[-1].get("requested_action") == "ESCALATE"
        )
        approval_required = bool(
            matched and (self.config.require_human_approval or agent_escalated)
        )
        if approval_required:
            invoice = self._invoice_or_raise(state["invoice_id"])
            invoice.status = Status.AWAITING_APPROVAL
            self.repo.save_invoice(invoice)
        update: WorkflowState = {
            "next_action": (
                "human-review"
                if approval_required or not matched
                else "post-payment-journal"
            )
        }
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
                "extraction_mode": invoice.extraction_mode,
                "extraction_attempts": invoice.extraction_attempts,
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
        try:
            po = self.erp.get_purchase_order(invoice.po_number)
        except ERPError as exc:
            self.audit.append(
                "invoice",
                invoice.id,
                "erp_lookup_failed",
                "agent:matcher",
                {"error_type": type(exc).__name__, "reason": str(exc)},
            )
            raise
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
            self.config.max_tax_pct,
            self.config.max_freight_pct,
            self.config.max_discount_pct,
        )
        self.repo.save_match(result)
        invoice.status = Status.MATCHED if result.matched else Status.EXCEPTION
        self.repo.save_invoice(invoice)
        explanation = (
            "Deterministic AP controls passed."
            if result.matched
            else "Deterministic AP controls found blocking variances; human review is required."
        )
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
        invoice = self._invoice_or_raise(invoice_id)
        if invoice.status in {
            Status.APPROVED,
            Status.REJECTED,
            Status.RESOLVED,
            Status.POSTED,
        }:
            raise ValueError(f"invoice in terminal status {invoice.status.value} cannot be re-matched")
        initial: WorkflowState = {
            "invoice_id": invoice_id,
            "require_goods_receipt": require_goods_receipt,
        }
        if self.graph is not None:
            state = self.graph.invoke(
                initial,
                config={"recursion_limit": self.config.agent_max_steps},
            )
        else:
            state = {**initial, **self._graph_retrieve_policy(initial)}
            state.update(self._graph_match(state))
            if self._route_after_match(state) == "post":
                state.update(self._graph_post(state))
            else:
                state.update(self._graph_finalize(state))
        return {
            key: state[key]
            for key in (
                "result",
                "next_action",
                "journal",
                "policies",
                "policy_ids",
                "explanation",
                "agent_decisions",
            )
            if key in state
        }

    def approve(self, invoice_id: str, actor: str, approved: bool, comment: str) -> dict[str, Any]:
        invoice = self._invoice_or_raise(invoice_id)
        if invoice.status not in {Status.EXCEPTION, Status.AWAITING_APPROVAL}:
            raise ValueError(f"invoice in status {invoice.status.value} is not awaiting a decision")
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

    def post(
        self,
        invoice_id: str,
        idempotency_key: str,
        actor: str = "agent:poster",
    ) -> dict[str, Any]:
        invoice = self._invoice_or_raise(invoice_id)
        existing = self.repo.get_journal(invoice_id, idempotency_key)
        if existing:
            return existing
        match = self.repo.latest_match(invoice_id)
        if match is None or (not match.matched and invoice.status != Status.APPROVED):
            raise ValueError("invoice does not have a successful match or explicit exception approval")
        if invoice.status not in {Status.MATCHED, Status.APPROVED}:
            raise ValueError(
                f"invoice status {invoice.status.value} is not authorized for posting"
            )
        if self.config.require_human_approval and invoice.status != Status.APPROVED:
            raise ValueError("human approval is required before posting")
        try:
            journal = self.erp.post_payment_journal(invoice, idempotency_key)
        except ERPError as exc:
            error = {"error_type": type(exc).__name__, "reason": str(exc)}
            self.audit.append(
                "invoice",
                invoice.id,
                "payment_journal_blocked",
                actor,
                {**error, "idempotency_key": idempotency_key},
            )
            self._persist(
                "payment_journal_blocked",
                {"invoice_id": invoice.id, "next_action": "retry-erp", "error": error},
            )
            raise
        invoice.status = Status.POSTED
        self.repo.save_invoice(invoice)
        self.repo.save_journal(journal)
        self.audit.append("invoice", invoice.id, "payment_journal_posted", actor, journal)
        return journal
