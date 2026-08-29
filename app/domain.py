from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Status(StrEnum):
    RECEIVED = "received"
    EXTRACTED = "extracted"
    MATCHED = "matched"
    EXCEPTION = "exception"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    POSTED = "posted"


@dataclass(slots=True)
class InvoiceLine:
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    po_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "quantity": str(self.quantity), "unit_price": str(self.unit_price), "amount": str(self.amount)}


@dataclass(slots=True)
class Invoice:
    vendor_id: str
    invoice_number: str
    invoice_date: date
    currency: str
    total: Decimal
    po_number: str | None
    lines: list[InvoiceLine]
    source_ref: str
    id: str = field(default_factory=lambda: uid("inv"))
    status: Status = Status.RECEIVED
    confidence: float = 0.0
    evidence: dict[str, str] = field(default_factory=dict)
    subtotal: Decimal | None = None
    tax_amount: Decimal = Decimal("0")
    freight_amount: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    extraction_mode: str = "unknown"
    extraction_attempts: list[dict[str, str]] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            invoice_date=self.invoice_date.isoformat(),
            total=str(self.total),
            subtotal=str(self.subtotal) if self.subtotal is not None else None,
            tax_amount=str(self.tax_amount),
            freight_amount=str(self.freight_amount),
            discount_amount=str(self.discount_amount),
            status=self.status.value,
        )
        data["lines"] = [line.to_dict() for line in self.lines]
        return data


@dataclass(slots=True)
class PurchaseOrderLine:
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    received_quantity: Decimal


@dataclass(slots=True)
class PurchaseOrder:
    number: str
    vendor_id: str
    currency: str
    lines: list[PurchaseOrderLine]

    @property
    def total(self) -> Decimal:
        return sum((line.quantity * line.unit_price for line in self.lines), Decimal("0"))


@dataclass(slots=True)
class Variance:
    code: str
    field: str
    expected: str | None
    actual: str | None
    variance_pct: float | None
    message: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MatchResult:
    invoice_id: str
    match_type: str
    matched: bool
    variances: list[Variance]
    po_number: str | None
    id: str = field(default_factory=lambda: uid("match"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "variances": [v.to_dict() for v in self.variances]}


@dataclass(slots=True)
class Remittance:
    customer_id: str
    reference: str
    amount: Decimal
    currency: str
    open_item_refs: list[str]
    source_ref: str
    id: str = field(default_factory=lambda: uid("rem"))
    status: Status = Status.RECEIVED

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "amount": str(self.amount), "status": self.status.value}
