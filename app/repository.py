from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from threading import RLock
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from app.domain import Invoice, InvoiceLine, MatchResult, PurchaseOrder, Remittance, Status, Variance
from app.embeddings import deterministic_embedding


POLICIES = [
    "Invoices must reference an active PO unless an authorized non-PO exception is approved.",
    "Duplicate vendor invoice numbers are blocked from posting.",
    "A three-way match requires goods received quantity to cover invoiced quantity.",
    "Only approved and matched invoices may be posted; posting must be idempotent.",
    "Customer remittances must match open AR items by customer, reference, currency, and amount.",
    "Cash application must be idempotent and revalidate open-item state immediately before posting.",
    "Unapplied, partial, duplicate, or ambiguous remittances require human resolution.",
]


def _invoice_from_dict(data: dict[str, Any]) -> Invoice:
    lines = [
        InvoiceLine(
            description=str(line["description"]),
            quantity=Decimal(str(line["quantity"])),
            unit_price=Decimal(str(line["unit_price"])),
            amount=Decimal(str(line["amount"])),
            po_line=line.get("po_line"),
        )
        for line in data.get("lines", [])
    ]
    return Invoice(
        vendor_id=str(data["vendor_id"]),
        invoice_number=str(data["invoice_number"]),
        invoice_date=date.fromisoformat(str(data["invoice_date"])),
        currency=str(data["currency"]),
        total=Decimal(str(data["total"])),
        po_number=data.get("po_number"),
        lines=lines,
        source_ref=str(data["source_ref"]),
        id=str(data["id"]),
        status=Status(str(data["status"])),
        confidence=float(data.get("confidence", 0.0)),
        evidence=dict(data.get("evidence", {})),
        subtotal=(
            Decimal(str(data["subtotal"])) if data.get("subtotal") is not None else None
        ),
        tax_amount=Decimal(str(data.get("tax_amount", "0"))),
        freight_amount=Decimal(str(data.get("freight_amount", "0"))),
        discount_amount=Decimal(str(data.get("discount_amount", "0"))),
        extraction_mode=str(data.get("extraction_mode", "unknown")),
        extraction_attempts=list(data.get("extraction_attempts", [])),
        created_at=str(data.get("created_at", "")),
    )


class MemoryRepository:
    """Thread-safe repository used for tests and zero-dependency local execution."""

    def __init__(self, embedding_fn: Callable[[str], list[float]] | None = None) -> None:
        self.backend = "memory"
        self.invoices: dict[str, Invoice] = {}
        self.purchase_orders: dict[str, PurchaseOrder] = {}
        self.matches: dict[str, MatchResult] = {}
        self.remittances: dict[str, Remittance] = {}
        self.remittance_results: dict[str, dict[str, Any]] = {}
        self.journals: dict[str, dict[str, Any]] = {}
        self.open_ar_items: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, dict[str, Any]] = {}
        self.workflow_states: dict[str, dict[str, Any]] = {}
        self.source_documents: dict[str, dict[str, str]] = {}
        self.policies = list(POLICIES)
        self.embedding_fn = embedding_fn or deterministic_embedding
        self._lock = RLock()

    def save_invoice(self, invoice: Invoice) -> Invoice:
        with self._lock:
            self.invoices[invoice.id] = invoice
            return invoice

    def save_source_document(
        self,
        source_ref: str,
        media_type: str,
        content_sha256: str,
    ) -> None:
        with self._lock:
            self.source_documents[source_ref] = {
                "source_ref": source_ref,
                "media_type": media_type,
                "content_sha256": content_sha256,
            }

    def get_invoice(self, invoice_id: str) -> Invoice | None:
        return self.invoices.get(invoice_id)

    def list_invoices(self, status: Status | None = None) -> list[Invoice]:
        invoices = list(self.invoices.values())
        return [invoice for invoice in invoices if invoice.status == status] if status else invoices

    def find_duplicate(self, vendor_id: str, invoice_number: str, exclude_id: str | None = None) -> Invoice | None:
        return next(
            (
                invoice
                for invoice in self.invoices.values()
                if invoice.vendor_id == vendor_id
                and invoice.invoice_number == invoice_number
                and invoice.id != exclude_id
            ),
            None,
        )

    def save_match(self, result: MatchResult) -> MatchResult:
        self.matches[result.id] = result
        return result

    def latest_match(self, invoice_id: str) -> MatchResult | None:
        return next(
            (result for result in reversed(list(self.matches.values())) if result.invoice_id == invoice_id),
            None,
        )

    def save_workflow_state(self, invoice_id: str, node: str, state: dict[str, Any]) -> None:
        self.workflow_states[invoice_id] = {"node": node, "state": state}

    def get_workflow_state(self, invoice_id: str) -> dict[str, Any] | None:
        return self.workflow_states.get(invoice_id)

    def save_finance_workflow_state(
        self,
        entity_id: str,
        workflow_type: str,
        node: str,
        status: str,
        state: dict[str, Any],
        source_ref: str | None = None,
    ) -> None:
        self.workflow_states[entity_id] = {
            "workflow_type": workflow_type,
            "node": node,
            "status": status,
            "source_ref": source_ref,
            "state": state,
        }

    def get_finance_workflow_state(self, entity_id: str) -> dict[str, Any] | None:
        return self.workflow_states.get(entity_id)

    def search_policies(self, query: str, top_k: int = 2) -> list[str]:
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        ranked = sorted(
            self.policies,
            key=lambda policy: len(words & set(re.findall(r"[a-z0-9]+", policy.lower()))),
            reverse=True,
        )
        return ranked[:top_k]

    def save_journal(self, journal: dict[str, Any]) -> None:
        self.journals[str(journal["journal_id"])] = journal

    def get_journal(self, invoice_id: str, idempotency_key: str) -> dict[str, Any] | None:
        return next(
            (
                journal
                for journal in self.journals.values()
                if journal.get("idempotency_key") == idempotency_key
                or journal.get("invoice_id") == invoice_id
            ),
            None,
        )

    def save_remittance(self, remittance: Remittance, result: dict[str, Any]) -> None:
        with self._lock:
            self.remittances[remittance.id] = remittance
            self.remittance_results[remittance.id] = result

    def get_remittance(self, remittance_id: str) -> Remittance | None:
        return self.remittances.get(remittance_id)

    def get_remittance_result(self, remittance_id: str) -> dict[str, Any]:
        return self.remittance_results.get(remittance_id, {})

    def find_duplicate_remittance(
        self,
        customer_id: str,
        reference: str,
        exclude_id: str | None = None,
    ) -> Remittance | None:
        return next(
            (
                remittance
                for remittance in self.remittances.values()
                if remittance.customer_id == customer_id
                and remittance.reference == reference
                and remittance.id != exclude_id
                and remittance.status != Status.REJECTED
            ),
            None,
        )

    def list_remittances(self, status: Status | None = None) -> list[dict[str, Any]]:
        remittances = reversed(list(self.remittances.values()))
        return [
            {
                "remittance": remittance.to_dict(),
                "result": self.remittance_results.get(remittance.id, {}),
            }
            for remittance in remittances
            if status is None or remittance.status == status
        ]

    def load_audit_events(self) -> list[Any]:
        return []


class PostgresRepository(MemoryRepository):
    """PostgreSQL repository with durable workflow state and pgvector policy retrieval."""

    def __init__(
        self,
        database_url: str,
        embedding_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        super().__init__(embedding_fn)
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(database_url, min_size=1, max_size=5, open=True)
        self.backend = "postgresql"
        self._ensure_runtime_schema()
        self._seed_policies()

    def _ensure_runtime_schema(self) -> None:
        """Apply additive prototype migrations for already-created Docker volumes."""
        with self.pool.connection() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS finance_workflow_runs (
                entity_id TEXT PRIMARY KEY,
                workflow_type TEXT NOT NULL CHECK (
                    workflow_type IN ('ap', 'ar', 'classification')
                ),
                source_ref TEXT,
                current_node TEXT NOT NULL,
                status TEXT NOT NULL,
                state JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )"""
            )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS finance_workflow_status_idx
                ON finance_workflow_runs(workflow_type, status)"""
            )

    def _seed_policies(self) -> None:
        with self.pool.connection() as conn:
            for index, policy in enumerate(self.policies, start=1):
                embedding = "[" + ",".join(
                    f"{value:.8f}" for value in self.embedding_fn(policy)
                ) + "]"
                conn.execute(
                    """INSERT INTO policy_documents (id, content, embedding, metadata)
                    VALUES (%s,%s,%s::vector,%s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET content=EXCLUDED.content, embedding=EXCLUDED.embedding""",
                    (
                        f"policy-{index}",
                        policy,
                        embedding,
                        json.dumps({"source": "finance control policy"}),
                    ),
                )

    def save_invoice(self, invoice: Invoice) -> Invoice:
        saved = super().save_invoice(invoice)
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO invoices (id,vendor_id,invoice_number,invoice_date,currency,total,po_number,status,confidence,source_ref,extracted_data)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,
                confidence=EXCLUDED.confidence, extracted_data=EXCLUDED.extracted_data""",
                (
                    invoice.id,
                    invoice.vendor_id,
                    invoice.invoice_number,
                    invoice.invoice_date,
                    invoice.currency,
                    invoice.total,
                    invoice.po_number,
                    invoice.status.value,
                    invoice.confidence,
                    invoice.source_ref,
                    json.dumps(invoice.to_dict()),
                ),
            )
        return saved

    def save_source_document(
        self,
        source_ref: str,
        media_type: str,
        content_sha256: str,
    ) -> None:
        super().save_source_document(source_ref, media_type, content_sha256)
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO source_documents (id,source_ref,media_type,content_sha256)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (source_ref) DO UPDATE SET media_type=EXCLUDED.media_type,
                content_sha256=EXCLUDED.content_sha256""",
                (uuid5(NAMESPACE_URL, source_ref), source_ref, media_type, content_sha256),
            )

    def get_invoice(self, invoice_id: str) -> Invoice | None:
        local = super().get_invoice(invoice_id)
        if local:
            return local
        with self.pool.connection() as conn:
            row = conn.execute("SELECT extracted_data FROM invoices WHERE id=%s", (invoice_id,)).fetchone()
        if not row:
            return None
        invoice = _invoice_from_dict(row[0])
        self.invoices[invoice.id] = invoice
        return invoice

    def list_invoices(self, status: Status | None = None) -> list[Invoice]:
        query = "SELECT id FROM invoices"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=%s"
            params = (status.value,)
        query += " ORDER BY created_at DESC"
        with self.pool.connection() as conn:
            ids = [row[0] for row in conn.execute(query, params).fetchall()]
        return [invoice for invoice_id in ids if (invoice := self.get_invoice(invoice_id)) is not None]

    def find_duplicate(self, vendor_id: str, invoice_number: str, exclude_id: str | None = None) -> Invoice | None:
        local = super().find_duplicate(vendor_id, invoice_number, exclude_id)
        if local:
            return local
        with self.pool.connection() as conn:
            if exclude_id is None:
                row = conn.execute(
                    "SELECT id FROM invoices WHERE vendor_id=%s AND invoice_number=%s LIMIT 1",
                    (vendor_id, invoice_number),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM invoices WHERE vendor_id=%s AND invoice_number=%s AND id<>%s LIMIT 1",
                    (vendor_id, invoice_number, exclude_id),
                ).fetchone()
        return self.get_invoice(row[0]) if row else None

    def save_match(self, result: MatchResult) -> MatchResult:
        saved = super().save_match(result)
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO match_results (id,invoice_id,match_type,matched,variances)
                VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (id) DO NOTHING""",
                (
                    result.id,
                    result.invoice_id,
                    result.match_type,
                    result.matched,
                    json.dumps([variance.to_dict() for variance in result.variances]),
                ),
            )
        return saved

    def latest_match(self, invoice_id: str) -> MatchResult | None:
        local = super().latest_match(invoice_id)
        if local:
            return local
        with self.pool.connection() as conn:
            row = conn.execute(
                """SELECT id, match_type, matched, variances, created_at
                FROM match_results WHERE invoice_id=%s ORDER BY created_at DESC LIMIT 1""",
                (invoice_id,),
            ).fetchone()
        if not row:
            return None
        invoice = self.get_invoice(invoice_id)
        result = MatchResult(
            invoice_id=invoice_id,
            match_type=row[1],
            matched=row[2],
            variances=[Variance(**variance) for variance in row[3]],
            po_number=invoice.po_number if invoice else None,
            id=row[0],
            created_at=row[4].isoformat(),
        )
        self.matches[result.id] = result
        return result

    def save_workflow_state(self, invoice_id: str, node: str, state: dict[str, Any]) -> None:
        super().save_workflow_state(invoice_id, node, state)
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO workflow_runs (invoice_id,current_node,state,updated_at)
                VALUES (%s,%s,%s::jsonb,now())
                ON CONFLICT (invoice_id) DO UPDATE SET current_node=EXCLUDED.current_node,
                state=EXCLUDED.state, updated_at=now()""",
                (invoice_id, node, json.dumps(state, default=str)),
            )

    def get_workflow_state(self, invoice_id: str) -> dict[str, Any] | None:
        local = super().get_workflow_state(invoice_id)
        if local:
            return local
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT current_node,state,updated_at FROM workflow_runs WHERE invoice_id=%s",
                (invoice_id,),
            ).fetchone()
        if not row:
            return None
        state = {"node": row[0], "state": row[1], "updated_at": row[2].isoformat()}
        self.workflow_states[invoice_id] = state
        return state

    def save_finance_workflow_state(
        self,
        entity_id: str,
        workflow_type: str,
        node: str,
        status: str,
        state: dict[str, Any],
        source_ref: str | None = None,
    ) -> None:
        MemoryRepository.save_finance_workflow_state(
            self,
            entity_id,
            workflow_type,
            node,
            status,
            state,
            source_ref,
        )
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO finance_workflow_runs
                (entity_id,workflow_type,source_ref,current_node,status,state,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,now())
                ON CONFLICT (entity_id) DO UPDATE SET workflow_type=EXCLUDED.workflow_type,
                source_ref=EXCLUDED.source_ref,current_node=EXCLUDED.current_node,
                status=EXCLUDED.status,state=EXCLUDED.state,updated_at=now()""",
                (
                    entity_id,
                    workflow_type,
                    source_ref,
                    node,
                    status,
                    json.dumps(state, default=str),
                ),
            )

    def get_finance_workflow_state(self, entity_id: str) -> dict[str, Any] | None:
        local = MemoryRepository.get_finance_workflow_state(self, entity_id)
        if local:
            return local
        with self.pool.connection() as conn:
            row = conn.execute(
                """SELECT workflow_type,current_node,status,source_ref,state,updated_at
                FROM finance_workflow_runs WHERE entity_id=%s""",
                (entity_id,),
            ).fetchone()
        if not row:
            return None
        state = {
            "workflow_type": row[0],
            "node": row[1],
            "status": row[2],
            "source_ref": row[3],
            "state": row[4],
            "updated_at": row[5].isoformat(),
        }
        self.workflow_states[entity_id] = state
        return state

    def search_policies(self, query: str, top_k: int = 2) -> list[str]:
        embedding = "[" + ",".join(
            f"{value:.8f}" for value in self.embedding_fn(query)
        ) + "]"
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT content FROM policy_documents ORDER BY embedding <=> %s::vector LIMIT %s",
                (embedding, top_k),
            ).fetchall()
        return [row[0] for row in rows]

    def save_journal(self, journal: dict[str, Any]) -> None:
        super().save_journal(journal)
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO payment_journals (id,invoice_id,idempotency_key,payload)
                VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (idempotency_key) DO NOTHING""",
                (
                    journal["journal_id"],
                    journal["invoice_id"],
                    journal["idempotency_key"],
                    json.dumps(journal),
                ),
            )

    def get_journal(self, invoice_id: str, idempotency_key: str) -> dict[str, Any] | None:
        local = super().get_journal(invoice_id, idempotency_key)
        if local:
            return local
        with self.pool.connection() as conn:
            row = conn.execute(
                """SELECT payload FROM payment_journals
                WHERE idempotency_key=%s OR invoice_id=%s
                ORDER BY CASE WHEN idempotency_key=%s THEN 0 ELSE 1 END, created_at
                LIMIT 1""",
                (idempotency_key, invoice_id, idempotency_key),
            ).fetchone()
        if not row:
            return None
        journal = row[0]
        self.journals[str(journal["journal_id"])] = journal
        return journal

    def save_remittance(self, remittance: Remittance, result: dict[str, Any]) -> None:
        super().save_remittance(remittance, result)
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO remittances (id,customer_id,reference,amount,currency,status,source_ref,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,payload=EXCLUDED.payload""",
                (
                    remittance.id,
                    remittance.customer_id,
                    remittance.reference,
                    remittance.amount,
                    remittance.currency,
                    remittance.status.value,
                    remittance.source_ref,
                    json.dumps({"remittance": remittance.to_dict(), "result": result}),
                ),
            )

    def get_remittance(self, remittance_id: str) -> Remittance | None:
        local = MemoryRepository.get_remittance(self, remittance_id)
        if local:
            return local
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM remittances WHERE id=%s",
                (remittance_id,),
            ).fetchone()
        if not row:
            return None
        data = row[0]["remittance"]
        remittance = Remittance(
            customer_id=data["customer_id"],
            reference=data["reference"],
            amount=Decimal(str(data["amount"])),
            currency=data["currency"],
            open_item_refs=list(data["open_item_refs"]),
            source_ref=data["source_ref"],
            id=data["id"],
            status=Status(data["status"]),
            confidence=float(data.get("confidence", 0.0)),
            evidence=dict(data.get("evidence", {})),
            extraction_mode=data.get("extraction_mode", "structured"),
            extraction_attempts=list(data.get("extraction_attempts", [])),
            created_at=data.get("created_at", ""),
        )
        self.remittances[remittance.id] = remittance
        self.remittance_results[remittance.id] = row[0].get("result", {})
        return remittance

    def get_remittance_result(self, remittance_id: str) -> dict[str, Any]:
        self.get_remittance(remittance_id)
        return MemoryRepository.get_remittance_result(self, remittance_id)

    def find_duplicate_remittance(
        self,
        customer_id: str,
        reference: str,
        exclude_id: str | None = None,
    ) -> Remittance | None:
        local = MemoryRepository.find_duplicate_remittance(
            self, customer_id, reference, exclude_id
        )
        if local:
            return local
        with self.pool.connection() as conn:
            if exclude_id is None:
                row = conn.execute(
                    """SELECT id FROM remittances
                    WHERE customer_id=%s AND reference=%s AND status<>%s LIMIT 1""",
                    (customer_id, reference, Status.REJECTED.value),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT id FROM remittances
                    WHERE customer_id=%s AND reference=%s AND status<>%s
                    AND id<>%s LIMIT 1""",
                    (customer_id, reference, Status.REJECTED.value, exclude_id),
                ).fetchone()
        return self.get_remittance(row[0]) if row else None

    def list_remittances(self, status: Status | None = None) -> list[dict[str, Any]]:
        query = "SELECT payload FROM remittances"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=%s"
            params = (status.value,)
        query += " ORDER BY created_at DESC"
        with self.pool.connection() as conn:
            return [row[0] for row in conn.execute(query, params).fetchall()]

    def persist_audit(self, event: Any) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO audit_events (id,entity_type,entity_id,action,actor,payload,previous_hash,event_hash,created_at)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT (id) DO NOTHING""",
                (
                    event.id,
                    event.entity_type,
                    event.entity_id,
                    event.action,
                    event.actor,
                    json.dumps(event.payload, default=str),
                    None if event.previous_hash == "GENESIS" else event.previous_hash,
                    event.event_hash,
                    event.timestamp,
                ),
            )

    def load_audit_events(self) -> list[Any]:
        from app.audit import AuditEvent

        with self.pool.connection() as conn:
            rows = conn.execute(
                """SELECT entity_type,entity_id,action,actor,payload,previous_hash,
                created_at,id,event_hash FROM audit_events ORDER BY sequence"""
            ).fetchall()
        return [
            AuditEvent(
                entity_type=row[0],
                entity_id=row[1],
                action=row[2],
                actor=row[3],
                payload=row[4],
                previous_hash=row[5] or "GENESIS",
                timestamp=row[6].isoformat(),
                id=row[7],
                event_hash=row[8],
            )
            for row in rows
        ]
