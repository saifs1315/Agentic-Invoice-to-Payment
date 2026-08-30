from __future__ import annotations

from decimal import Decimal
from typing import Any, TypedDict

from app.ar_matching import match_remittance
from app.audit import AuditLedger
from app.config import Settings
from app.context import ContextRetriever
from app.domain import Remittance, Status
from app.erp import ERPClient, ERPError
from app.repository import MemoryRepository


class ARWorkflowState(TypedDict, total=False):
    remittance_id: str
    policies: list[str]
    result: dict[str, Any]
    next_action: str
    application: dict[str, Any]


class RemittanceWorkflow:
    """LangGraph-orchestrated AR mirror with deterministic cash-allocation controls."""

    def __init__(
        self,
        repo: MemoryRepository,
        audit: AuditLedger,
        erp: ERPClient,
        config: Settings,
    ) -> None:
        self.repo, self.audit, self.erp, self.config = repo, audit, erp, config
        self.context = ContextRetriever(repo)
        self.graph = self._build_graph()

    def _build_graph(self):
        try:
            from langgraph.graph import END, START, StateGraph

            graph = StateGraph(ARWorkflowState)
            graph.add_node("retrieve_ar_policy", self._graph_retrieve_policy)
            graph.add_node("deterministic_ar_match", self._graph_match)
            graph.add_node("apply_cash", self._graph_apply)
            graph.add_node("ar_review", self._graph_finalize)
            graph.add_edge(START, "retrieve_ar_policy")
            graph.add_edge("retrieve_ar_policy", "deterministic_ar_match")
            graph.add_conditional_edges(
                "deterministic_ar_match",
                self._route_after_match,
                {"apply": "apply_cash", "review": "ar_review"},
            )
            graph.add_edge("apply_cash", END)
            graph.add_edge("ar_review", END)
            return graph.compile()
        except ImportError:
            return None

    def _remittance_or_raise(self, remittance_id: str) -> Remittance:
        remittance = self.repo.get_remittance(remittance_id)
        if remittance is None:
            raise KeyError(remittance_id)
        return remittance

    def _persist(self, node: str, state: ARWorkflowState) -> None:
        remittance = self._remittance_or_raise(state["remittance_id"])
        self.repo.save_finance_workflow_state(
            remittance.id,
            "ar",
            node,
            remittance.status.value,
            dict(state),
            remittance.source_ref,
        )

    def ingest(self, remittance: Remittance, run: bool = True) -> dict[str, Any]:
        remittance.status = Status.EXTRACTED
        self.repo.save_remittance(remittance, {})
        self.audit.append(
            "remittance",
            remittance.id,
            "remittance_extracted",
            "agent:document-extractor",
            {
                "source_ref": remittance.source_ref,
                "confidence": remittance.confidence,
                "evidence": remittance.evidence,
                "extraction_mode": remittance.extraction_mode,
                "extraction_attempts": remittance.extraction_attempts,
            },
        )
        initial: ARWorkflowState = {"remittance_id": remittance.id}
        self._persist("ingested", initial)
        if not run:
            return {"remittance": remittance.to_dict(), "next_action": "match-open-items"}
        state = self.run(remittance.id)
        return {"remittance": remittance.to_dict(), **state}

    def _graph_retrieve_policy(self, state: ARWorkflowState) -> ARWorkflowState:
        update: ARWorkflowState = {
            "policies": self.context.retrieve(
                "customer remittance open AR items currency amount cash application idempotent"
            )
        }
        self._persist("retrieve_ar_policy", {**state, **update})
        return update

    def _graph_match(self, state: ARWorkflowState) -> ARWorkflowState:
        remittance = self._remittance_or_raise(state["remittance_id"])
        try:
            open_items = self.erp.get_open_items(remittance.customer_id)
        except ERPError as exc:
            self.audit.append(
                "remittance",
                remittance.id,
                "ar_erp_lookup_failed",
                "agent:ar-matcher",
                {"error_type": type(exc).__name__, "reason": str(exc)},
            )
            raise
        duplicate = (
            self.repo.find_duplicate_remittance(
                remittance.customer_id,
                remittance.reference,
                exclude_id=remittance.id,
            )
            is not None
        )
        result = match_remittance(remittance, open_items, duplicate)
        remittance.status = Status.MATCHED if result["matched"] else Status.EXCEPTION
        self.repo.save_remittance(remittance, result)
        self.audit.append(
            "remittance",
            remittance.id,
            "ar_match_completed",
            "agent:ar-matcher",
            {"result": result, "policies": state.get("policies", [])},
        )
        update: ARWorkflowState = {"result": result}
        self._persist("deterministic_ar_match", {**state, **update})
        return update

    def _route_after_match(self, state: ARWorkflowState) -> str:
        if (
            state["result"]["matched"]
            and self.config.auto_post_enabled
            and not self.config.require_human_approval
        ):
            return "apply"
        return "review"

    def _graph_apply(self, state: ARWorkflowState) -> ARWorkflowState:
        remittance = self._remittance_or_raise(state["remittance_id"])
        try:
            application = self.erp.apply_cash(
                remittance.customer_id,
                remittance.amount,
                remittance.currency,
                remittance.open_item_refs,
                f"auto:{remittance.id}",
                remittance.id,
            )
        except ERPError as exc:
            error = {"error_type": type(exc).__name__, "reason": str(exc)}
            self.audit.append(
                "remittance",
                remittance.id,
                "cash_application_blocked",
                "agent:ar-poster",
                error,
            )
            self._persist(
                "cash_application_blocked",
                {"remittance_id": remittance.id, "next_action": "retry-erp"},
            )
            raise
        result = {**state["result"], **application}
        remittance.status = Status.POSTED if application["applied"] else Status.EXCEPTION
        self.repo.save_remittance(remittance, result)
        self.audit.append(
            "remittance",
            remittance.id,
            "cash_application_posted" if application["applied"] else "cash_application_blocked",
            "agent:ar-poster",
            application,
        )
        next_action = "cash-applied" if application["applied"] else "human-review"
        update: ARWorkflowState = {
            "result": result,
            "application": application,
            "next_action": next_action,
        }
        self._persist("apply_cash", {**state, **update})
        return update

    def _graph_finalize(self, state: ARWorkflowState) -> ARWorkflowState:
        remittance = self._remittance_or_raise(state["remittance_id"])
        if state["result"]["matched"] and self.config.require_human_approval:
            remittance.status = Status.AWAITING_APPROVAL
            self.repo.save_remittance(remittance, state["result"])
        update: ARWorkflowState = {"next_action": "human-review"}
        self._persist("ar_review", {**state, **update})
        return update

    def run(self, remittance_id: str) -> dict[str, Any]:
        initial: ARWorkflowState = {"remittance_id": remittance_id}
        if self.graph is not None:
            state = self.graph.invoke(initial)
        else:
            state = {**initial, **self._graph_retrieve_policy(initial)}
            state.update(self._graph_match(state))
            if self._route_after_match(state) == "apply":
                state.update(self._graph_apply(state))
            else:
                state.update(self._graph_finalize(state))
        return {
            key: state[key]
            for key in ("result", "next_action", "application", "policies")
            if key in state
        }

    def decide(
        self,
        remittance_id: str,
        action: str,
        actor: str,
        comment: str,
        corrections: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        remittance = self._remittance_or_raise(remittance_id)
        if remittance.status not in {Status.EXCEPTION, Status.AWAITING_APPROVAL}:
            raise ValueError("remittance is not awaiting human review")
        if action == "REJECT":
            remittance.status = Status.REJECTED
            self.repo.save_remittance(remittance, self.repo.get_remittance_result(remittance.id))
            result = {"remittance_id": remittance.id, "status": remittance.status.value}
        elif action == "MARK_MANUALLY_RESOLVED":
            remittance.status = Status.RESOLVED
            self.repo.save_remittance(remittance, self.repo.get_remittance_result(remittance.id))
            result = {"remittance_id": remittance.id, "status": remittance.status.value}
        elif action == "RETRY_WITH_CORRECTION":
            if not corrections:
                raise ValueError("corrections are required for retry")
            for field in ("customer_id", "reference", "currency"):
                if corrections.get(field) is not None:
                    setattr(remittance, field, str(corrections[field]))
            if corrections.get("amount") is not None:
                remittance.amount = Decimal(str(corrections["amount"]))
            if corrections.get("open_item_refs") is not None:
                remittance.open_item_refs = [str(item) for item in corrections["open_item_refs"]]
            remittance.status = Status.EXTRACTED
            self.repo.save_remittance(remittance, {})
            result = {"remittance": remittance.to_dict(), **self.run(remittance.id)}
        elif action == "APPROVE_APPLY":
            current = self.repo.get_remittance_result(remittance.id)
            if remittance.status != Status.AWAITING_APPROVAL or not current.get("matched"):
                raise ValueError("only a valid matched remittance can be approved for cash application")
            result = {**current, **self._graph_apply({"remittance_id": remittance.id, "result": current})}
        else:
            raise ValueError("unsupported AR review action")
        self.audit.append(
            "remittance",
            remittance.id,
            f"human_{action.lower()}",
            actor,
            {"comment": comment, "corrections": corrections or {}},
        )
        return result
