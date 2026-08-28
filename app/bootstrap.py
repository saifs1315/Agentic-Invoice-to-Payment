from app.audit import AuditLedger
from app.config import settings
from app.erp import MockERP
from app.observability import configure_observability
from app.repository import MemoryRepository, PostgresRepository
from app.workflow import InvoiceWorkflow

try:
    repo = PostgresRepository(settings.database_url) if settings.database_url.startswith("postgresql") else MemoryRepository()
except Exception:
    repo = MemoryRepository()
audit = AuditLedger(getattr(repo, "persist_audit", None))
erp = MockERP()
workflow = InvoiceWorkflow(repo, audit, erp, settings)
observability_enabled = configure_observability()
