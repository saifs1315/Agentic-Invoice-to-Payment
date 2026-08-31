import logging
from importlib.util import find_spec

from app.agent_runtime import AIRuntimeUnavailableError, create_agent_runtime
from app.audit import AuditLedger
from app.config import settings
from app.context import POLICY_QUERIES
from app.ar_workflow import RemittanceWorkflow
from app.erp import HttpERPClient, MockERP
from app.orchestrator import FinanceOrchestrator
from app.observability import configure_observability
from app.repository import MemoryRepository, PostgresRepository
from app.workflow import InvoiceWorkflow

logger = logging.getLogger(__name__)
observability_enabled = configure_observability()
agent_runtime = create_agent_runtime(settings)

if settings.database_url.startswith("postgresql"):
    try:
        repo = PostgresRepository(settings.database_url, agent_runtime.embed)
    except AIRuntimeUnavailableError:
        logger.exception("Mandatory AI embedding runtime failed while seeding policy vectors")
        raise
    except Exception:
        logger.exception("Configured PostgreSQL repository failed to initialize")
        raise
else:
    repo = MemoryRepository(agent_runtime.embed)

# With a single-model Ollama budget, cache the two fixed semantic retrieval queries
# while the embedding model is already resident during repository initialization.
for policy_query in POLICY_QUERIES.values():
    try:
        agent_runtime.embed(policy_query)
    except Exception:
        logger.exception("AI embedding warm-up failed; health checks will expose runtime state")
audit = AuditLedger(getattr(repo, "persist_audit", None), repo.load_audit_events())
erp = (
    HttpERPClient(settings.erp_base_url, settings.erp_timeout_seconds)
    if settings.erp_mode == "http"
    else MockERP()
)
workflow = InvoiceWorkflow(repo, audit, erp, settings, agent_runtime)
ar_workflow = RemittanceWorkflow(repo, audit, erp, settings, agent_runtime)
orchestrator = FinanceOrchestrator(repo, audit, workflow, ar_workflow, agent_runtime)
ai_capabilities = agent_runtime.capabilities()
runtime_capabilities = {
    "ai_required": True,
    "agent_runtime": ai_capabilities,
    "supervisor_agent_ready": ai_capabilities["ready"],
    "ap_agent_ready": ai_capabilities["ready"],
    "ar_agent_ready": ai_capabilities["ready"],
    "langgraph_active": all(
        graph is not None for graph in (workflow.graph, ar_workflow.graph, orchestrator.graph)
    ),
    "llamaindex_active": all(
        context.index is not None for context in (workflow.context, ar_workflow.context)
    ),
    "docling_available": find_spec("docling") is not None,
    "ollama_client_available": find_spec("ollama") is not None,
    "phoenix_active": observability_enabled,
}
if not runtime_capabilities["langgraph_active"]:
    logger.warning("LangGraph is unavailable; fixed workflow execution fallback is active")
if not runtime_capabilities["llamaindex_active"]:
    logger.warning("LlamaIndex semantic index is unavailable")
