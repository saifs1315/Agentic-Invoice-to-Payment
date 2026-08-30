import logging
from importlib.util import find_spec

from app.audit import AuditLedger
from app.config import settings
from app.ar_workflow import RemittanceWorkflow
from app.erp import HttpERPClient, MockERP
from app.orchestrator import FinanceOrchestrator
from app.observability import configure_observability
from app.repository import MemoryRepository, PostgresRepository
from app.workflow import InvoiceWorkflow

logger = logging.getLogger(__name__)
repository_fallback_error: str | None = None

try:
    repo = (
        PostgresRepository(settings.database_url)
        if settings.database_url.startswith("postgresql")
        else MemoryRepository()
    )
except Exception as exc:
    repository_fallback_error = type(exc).__name__
    logger.exception(
        "Configured PostgreSQL repository failed to initialize; using non-durable memory storage"
    )
    repo = MemoryRepository()
audit = AuditLedger(getattr(repo, "persist_audit", None), repo.load_audit_events())
erp = (
    HttpERPClient(settings.erp_base_url, settings.erp_timeout_seconds)
    if settings.erp_mode == "http"
    else MockERP()
)
workflow = InvoiceWorkflow(repo, audit, erp, settings)
ar_workflow = RemittanceWorkflow(repo, audit, erp, settings)
orchestrator = FinanceOrchestrator(repo, audit, workflow, ar_workflow)
observability_enabled = configure_observability()
runtime_capabilities = {
    "langgraph_active": all(
        graph is not None for graph in (workflow.graph, ar_workflow.graph, orchestrator.graph)
    ),
    "llamaindex_active": all(
        context.index is not None for context in (workflow.context, ar_workflow.context)
    ),
    "docling_available": find_spec("docling") is not None,
    "ollama_client_available": find_spec("ollama") is not None,
    "llm_extraction_enabled": settings.llm_extraction_enabled,
    "llm_explanations_enabled": settings.llm_explanations_enabled,
    "phoenix_active": observability_enabled,
    "repository_degraded": repository_fallback_error is not None,
    "repository_fallback_error": repository_fallback_error,
}
if not runtime_capabilities["langgraph_active"]:
    logger.warning("LangGraph is unavailable; workflows are running the deterministic fallback")
if not runtime_capabilities["llamaindex_active"]:
    logger.warning("LlamaIndex is unavailable; policy retrieval is running the lexical fallback")
