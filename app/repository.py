from __future__ import annotations

from threading import RLock

from app.domain import Invoice, MatchResult, PurchaseOrder, Remittance


class MemoryRepository:
    """Thread-safe demo repository; the same interface is implemented by PostgreSQL in production."""

    def __init__(self) -> None:
        self.backend = "memory"
        self.invoices: dict[str, Invoice] = {}
        self.purchase_orders: dict[str, PurchaseOrder] = {}
        self.matches: dict[str, MatchResult] = {}
        self.remittances: dict[str, Remittance] = {}
        self.journals: dict[str, dict] = {}
        self.open_ar_items: dict[str, dict] = {}
        self.idempotency: dict[str, dict] = {}
        self._lock = RLock()

    def save_invoice(self, invoice: Invoice) -> Invoice:
        with self._lock:
            self.invoices[invoice.id] = invoice
            return invoice

    def find_duplicate(self, vendor_id: str, invoice_number: str, exclude_id: str | None = None) -> Invoice | None:
        return next((i for i in self.invoices.values() if i.vendor_id == vendor_id and i.invoice_number == invoice_number and i.id != exclude_id), None)

    def save_match(self, result: MatchResult) -> MatchResult:
        self.matches[result.id] = result
        return result


class PostgresRepository(MemoryRepository):
    """Write-through PostgreSQL adapter; in-memory objects keep the prototype responsive."""

    def __init__(self, database_url: str) -> None:
        super().__init__()
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(database_url, min_size=1, max_size=5, open=True)
        self.backend = "postgresql"

    def save_invoice(self, invoice: Invoice) -> Invoice:
        saved = super().save_invoice(invoice)
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO invoices (id,vendor_id,invoice_number,invoice_date,currency,total,po_number,status,confidence,source_ref,extracted_data)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (vendor_id,invoice_number) DO UPDATE
                SET status=EXCLUDED.status, extracted_data=EXCLUDED.extracted_data
                WHERE invoices.id=EXCLUDED.id""",
                (invoice.id, invoice.vendor_id, invoice.invoice_number, invoice.invoice_date, invoice.currency, invoice.total, invoice.po_number, invoice.status.value, invoice.confidence, invoice.source_ref, __import__("json").dumps(invoice.to_dict())),
            )
        return saved

    def find_duplicate(self, vendor_id: str, invoice_number: str, exclude_id: str | None = None) -> Invoice | None:
        local = super().find_duplicate(vendor_id, invoice_number, exclude_id)
        if local:
            return local
        with self.pool.connection() as conn:
            row = conn.execute("SELECT id FROM invoices WHERE vendor_id=%s AND invoice_number=%s AND (%s IS NULL OR id<>%s) LIMIT 1", (vendor_id, invoice_number, exclude_id, exclude_id)).fetchone()
        return self.invoices.get(row[0]) if row else None

    def save_match(self, result: MatchResult) -> MatchResult:
        saved = super().save_match(result)
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO match_results (id,invoice_id,match_type,matched,variances) VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (id) DO NOTHING",
                (result.id, result.invoice_id, result.match_type, result.matched, __import__("json").dumps([v.to_dict() for v in result.variances])),
            )
        return saved

    def persist_audit(self, event) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO audit_events (id,entity_type,entity_id,action,actor,payload,previous_hash,event_hash,created_at)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                (event.id, event.entity_type, event.entity_id, event.action, event.actor, __import__("json").dumps(event.payload, default=str), None if event.previous_hash == "GENESIS" else event.previous_hash, event.event_hash, event.timestamp),
            )
