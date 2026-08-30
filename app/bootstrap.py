from app.audit import AuditLedger
from app.config import settings
from app.ar_workflow import RemittanceWorkflow
from app.erp import HttpERPClient, MockERP
from app.orchestrator import FinanceOrchestrator
from app.observability import configure_observability
from app.repository import MemoryRepository, PostgresRepository
from app.workflow import InvoiceWorkflow

try:
    repo = PostgresRepository(settings.database_url) if settings.database_url.startswith("postgresql") else MemoryRepository()
except Exception:
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
