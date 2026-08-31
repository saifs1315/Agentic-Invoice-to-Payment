from __future__ import annotations

from typing import Any, TypedDict

from app.agent_runtime import AgentRuntime
from app.ar_workflow import RemittanceWorkflow
from app.audit import AuditLedger
from app.document_processing import UnifiedDocumentProcessor
from app.domain import CanonicalDocument, DocumentKind, Invoice, Remittance, SourceEnvelope
from app.extraction import extract_invoice_from_document, extract_remittance_from_document
from app.repository import MemoryRepository
from app.workflow import InvoiceWorkflow


class OrchestratorState(TypedDict, total=False):
    envelope: SourceEnvelope
    document: CanonicalDocument
    document_kind: str
    payload: Invoice | Remittance
    response: dict[str, Any]
    supervisor_decision: dict[str, Any]


class FinanceOrchestrator:
    """Parent graph that classifies once and dispatches to the AP or AR subgraph."""

    def __init__(
        self,
        repo: MemoryRepository,
        audit: AuditLedger,
        ap_workflow: InvoiceWorkflow,
        ar_workflow: RemittanceWorkflow,
        runtime: AgentRuntime | None = None,
    ) -> None:
        self.repo = repo
        self.audit = audit
        self.ap_workflow = ap_workflow
        self.ar_workflow = ar_workflow
        self.runtime = runtime or ap_workflow.runtime
        self.processor = UnifiedDocumentProcessor()
        self.graph = self._build_graph()

    def _build_graph(self):
        try:
            from langgraph.graph import END, START, StateGraph

            graph = StateGraph(OrchestratorState)
            graph.add_node("register_source", self._register_source)
            graph.add_node("process_document", self._process_document)
            graph.add_node("classify_document", self._classify_document)
            graph.add_node("extract_typed_payload", self._extract_typed_payload)
            graph.add_node("dispatch_ap_subgraph", self._dispatch_ap)
            graph.add_node("dispatch_ar_subgraph", self._dispatch_ar)
            graph.add_node("classification_review", self._classification_review)
            graph.add_edge(START, "register_source")
            graph.add_edge("register_source", "process_document")
            graph.add_edge("process_document", "classify_document")
            graph.add_conditional_edges(
                "classify_document",
                self._route_classification,
                {"extract": "extract_typed_payload", "review": "classification_review"},
            )
            graph.add_conditional_edges(
                "extract_typed_payload",
                self._route_domain,
                {"ap": "dispatch_ap_subgraph", "ar": "dispatch_ar_subgraph"},
            )
            graph.add_edge("dispatch_ap_subgraph", END)
            graph.add_edge("dispatch_ar_subgraph", END)
            graph.add_edge("classification_review", END)
            return graph.compile()
        except ImportError:
            return None

    def _register_source(self, state: OrchestratorState) -> OrchestratorState:
        envelope = state["envelope"]
        self.repo.save_source_document(
            envelope.source_ref,
            envelope.media_type,
            envelope.content_sha256,
        )
        return {}

    def _process_document(self, state: OrchestratorState) -> OrchestratorState:
        document = self.processor.process(state["envelope"])
        self.audit.append(
            "source_document",
            document.source_ref,
            "agent_tool_completed",
            "tool:unified-document-processor",
            {
                "tool": "process_document",
                "processing_mode": document.processing_mode,
                "processing_attempts": document.processing_attempts,
                "deterministic_kind": document.kind.value,
            },
        )
        return {"document": document}

    def _classify_document(self, state: OrchestratorState) -> OrchestratorState:
        document = state["document"]
        deterministic_kind = document.kind
        decision = self.runtime.supervise(
            {
                "filename": document.filename,
                "media_type": document.media_type,
                "processing_mode": document.processing_mode,
                "deterministic_kind": deterministic_kind.value,
                "classification_reason": document.classification_reason,
                "document_text_excerpt": document.text[:6000],
                "allowed_actions": [
                    "DISPATCH_AP",
                    "DISPATCH_AR",
                    "ESCALATE_CLASSIFICATION",
                ],
            }
        )
        expected_action = {
            DocumentKind.AP_INVOICE: "DISPATCH_AP",
            DocumentKind.AR_REMITTANCE: "DISPATCH_AR",
            DocumentKind.UNKNOWN: None,
        }[deterministic_kind]
        if expected_action is not None and decision.action != expected_action:
            document.kind = DocumentKind.UNKNOWN
            document.classification_reason = "agent-deterministic-classification-conflict"
        elif decision.action == "DISPATCH_AP":
            document.kind = DocumentKind.AP_INVOICE
            document.classification_reason = "supervisor-agent-ap-dispatch"
        elif decision.action == "DISPATCH_AR":
            document.kind = DocumentKind.AR_REMITTANCE
            document.classification_reason = "supervisor-agent-ar-dispatch"
        else:
            document.kind = DocumentKind.UNKNOWN
            document.classification_reason = "supervisor-agent-escalation"
        decision_data = decision.model_dump()
        self.audit.append(
            "source_document",
            document.source_ref,
            "document_classified",
            "agent:finance-orchestrator",
            {
                "kind": document.kind.value,
                "deterministic_kind": deterministic_kind.value,
                "reason": document.classification_reason,
                "processing_mode": document.processing_mode,
                "processing_attempts": document.processing_attempts,
                "supervisor_decision": decision_data,
            },
        )
        return {
            "document_kind": document.kind.value,
            "supervisor_decision": decision_data,
        }

    @staticmethod
    def _route_classification(state: OrchestratorState) -> str:
        return "review" if state["document_kind"] == DocumentKind.UNKNOWN.value else "extract"

    def _extract_typed_payload(self, state: OrchestratorState) -> OrchestratorState:
        document = state["document"]
        if document.kind == DocumentKind.AP_INVOICE:
            payload: Invoice | Remittance = extract_invoice_from_document(
                document,
                self.runtime,
            )
        else:
            payload = extract_remittance_from_document(document, self.runtime)
        return {"payload": payload}

    @staticmethod
    def _route_domain(state: OrchestratorState) -> str:
        return "ap" if state["document_kind"] == DocumentKind.AP_INVOICE.value else "ar"

    def _dispatch_ap(self, state: OrchestratorState) -> OrchestratorState:
        invoice = state["payload"]
        if not isinstance(invoice, Invoice):
            raise TypeError("AP route did not receive an invoice payload")
        ingested = self.ap_workflow.ingest(invoice)
        matched = self.ap_workflow.match(invoice.id)
        return {
            "response": {
                "workflow_type": "ap",
                "entity_id": invoice.id,
                "classification": self._classification_response(
                    state["document"], state.get("supervisor_decision")
                ),
                **ingested,
                **matched,
            }
        }

    def _dispatch_ar(self, state: OrchestratorState) -> OrchestratorState:
        remittance = state["payload"]
        if not isinstance(remittance, Remittance):
            raise TypeError("AR route did not receive a remittance payload")
        response = self.ar_workflow.ingest(remittance, run=True)
        return {
            "response": {
                "workflow_type": "ar",
                "entity_id": remittance.id,
                "classification": self._classification_response(
                    state["document"], state.get("supervisor_decision")
                ),
                **response,
            }
        }

    def _classification_review(self, state: OrchestratorState) -> OrchestratorState:
        document = state["document"]
        entity_id = f"doc_{state['envelope'].content_sha256[:16]}"
        response = {
            "workflow_type": "classification",
            "entity_id": entity_id,
            "status": "exception",
            "next_action": "human-classification",
            "classification": self._classification_response(
                document, state.get("supervisor_decision")
            ),
        }
        self.repo.save_finance_workflow_state(
            entity_id,
            "classification",
            "classification_review",
            "exception",
            response,
            document.source_ref,
        )
        return {"response": response}

    @staticmethod
    def _classification_response(
        document: CanonicalDocument,
        supervisor_decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = {
            "kind": document.kind.value,
            "reason": document.classification_reason,
            "processing_mode": document.processing_mode,
            "evidence": document.processing_attempts,
        }
        if supervisor_decision is not None:
            response["supervisor_decision"] = supervisor_decision
        return response

    def ingest(self, envelope: SourceEnvelope) -> dict[str, Any]:
        initial: OrchestratorState = {"envelope": envelope}
        if self.graph is not None:
            state = self.graph.invoke(initial)
        else:
            state = dict(initial)
            state.update(self._register_source(state))
            state.update(self._process_document(state))
            state.update(self._classify_document(state))
            if self._route_classification(state) == "review":
                state.update(self._classification_review(state))
            else:
                state.update(self._extract_typed_payload(state))
                if self._route_domain(state) == "ap":
                    state.update(self._dispatch_ap(state))
                else:
                    state.update(self._dispatch_ar(state))
        return state["response"]

    def ingest_only(
        self,
        envelope: SourceEnvelope,
        expected_kind: DocumentKind,
    ) -> dict[str, Any]:
        """Run the supervisor and extraction tool, but leave domain execution explicit."""
        state: OrchestratorState = {"envelope": envelope}
        state.update(self._register_source(state))
        state.update(self._process_document(state))
        state.update(self._classify_document(state))
        if state["document_kind"] != expected_kind.value:
            raise ValueError(
                f"supervisor did not classify the document as {expected_kind.value}"
            )
        state.update(self._extract_typed_payload(state))
        payload = state["payload"]
        if expected_kind == DocumentKind.AP_INVOICE:
            if not isinstance(payload, Invoice):
                raise TypeError("AP route did not receive an invoice payload")
            response = self.ap_workflow.ingest(payload)
        else:
            if not isinstance(payload, Remittance):
                raise TypeError("AR route did not receive a remittance payload")
            response = self.ar_workflow.ingest(payload, run=False)
        return {
            "workflow_type": "ap" if expected_kind == DocumentKind.AP_INVOICE else "ar",
            "entity_id": payload.id,
            "classification": self._classification_response(
                state["document"], state.get("supervisor_decision")
            ),
            **response,
        }
