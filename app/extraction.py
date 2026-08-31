from __future__ import annotations

import gc
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from pydantic import BaseModel, Field, field_validator

from app.domain import (
    MAX_MONETARY_AMOUNT,
    MAX_QUANTITY,
    CanonicalDocument,
    Invoice,
    InvoiceLine,
    Remittance,
)

if TYPE_CHECKING:
    from app.agent_runtime import AgentRuntime


FIELD_PATTERNS = {
    "vendor_id": r"(?:vendor(?:[_ ]id)?|supplier)\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_-]+)",
    "invoice_number": r"invoice(?:[_ ](?:number|no))?\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_-]+)",
    "invoice_date": r"(?:invoice[_ ]date|date)\s*[:#]\s*(?:\|\s*)?(\d{4}-\d{2}-\d{2})",
    "po_number": r"(?:po|purchase[_ ]order)(?:[_ ](?:number|no))?\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_-]+)",
    "currency": r"currency\s*[:#]\s*(?:\|\s*)?([A-Z]{3})",
    "total": r"(?:invoice[_ ]total|\btotal)\s*[:#]\s*(?:\|\s*)?([0-9]+(?:\.[0-9]{1,2})?)",
    "subtotal": r"subtotal\s*[:#]\s*(?:\|\s*)?([0-9]+(?:\.[0-9]{1,2})?)",
    "tax_amount": r"(?:tax|tax[_ ]amount)\s*[:#]\s*(?:\|\s*)?([0-9]+(?:\.[0-9]{1,2})?)",
    "freight_amount": r"(?:freight|shipping)\s*[:#]\s*(?:\|\s*)?([0-9]+(?:\.[0-9]{1,2})?)",
    "discount_amount": r"discount\s*[:#]\s*(?:\|\s*)?([0-9]+(?:\.[0-9]{1,2})?)",
}

LINE_PATTERN = re.compile(
    r"line\s*\d+\s*:\s*(?P<description>[^|;\n]+)\s*(?:[|;]|\r?\n)\s*"
    r"qty(?:uantity)?\s*[:#]?\s*(?P<quantity>[0-9.]+)\s*(?:[|;]|\r?\n)\s*"
    r"unit\s*price\s*[:#]?\s*(?P<unit_price>[0-9.]+)\s*(?:[|;]|\r?\n)\s*"
    r"amount\s*[:#]?\s*(?P<amount>[0-9.]+)\s*(?:[|;]|\r?\n)\s*"
    r"po\s*line\s*[:#]?\s*(?P<po_line>[0-9]+)",
    re.IGNORECASE,
)

REMITTANCE_PATTERNS = {
    "customer_id": r"customer(?:[_ ]id)?\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_-]+)",
    "reference": r"(?:remittance|payment)(?:[_ ](?:reference|ref))?\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_-]+)",
    "amount": r"(?:payment[_ ]amount|amount)\s*[:#]\s*(?:\|\s*)?([0-9]+(?:\.[0-9]{1,2})?)",
    "currency": r"currency\s*[:#]\s*(?:\|\s*)?([A-Z]{3})",
    "open_item_refs": r"(?:open[_ ]items?|invoice[_ ]refs?)\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_, -]+)",
}


class InvoiceLinePayload(BaseModel):
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0, le=MAX_QUANTITY)
    unit_price: Decimal = Field(ge=0, le=MAX_MONETARY_AMOUNT)
    amount: Decimal = Field(ge=0, le=MAX_MONETARY_AMOUNT)
    po_line: int | None = Field(default=None, ge=1)


class InvoicePayload(BaseModel):
    vendor_id: str = Field(min_length=2)
    invoice_number: str = Field(min_length=1)
    invoice_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    total: Decimal = Field(gt=0, le=MAX_MONETARY_AMOUNT)
    subtotal: Decimal | None = Field(default=None, ge=0, le=MAX_MONETARY_AMOUNT)
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0, le=MAX_MONETARY_AMOUNT)
    freight_amount: Decimal = Field(default=Decimal("0"), ge=0, le=MAX_MONETARY_AMOUNT)
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0, le=MAX_MONETARY_AMOUNT)
    po_number: str | None = None
    lines: list[InvoiceLinePayload] = Field(default_factory=list)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> str:
        return str(value).upper()


class RemittancePayload(BaseModel):
    customer_id: str = Field(min_length=2)
    reference: str = Field(min_length=1)
    amount: Decimal = Field(gt=0, le=MAX_MONETARY_AMOUNT)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    open_item_refs: list[str] = Field(min_length=1)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> str:
        return str(value).upper()

    @field_validator("open_item_refs", mode="before")
    @classmethod
    def normalize_refs(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,;]", value) if item.strip()]
        return [str(item).strip() for item in value]


@contextmanager
def _temporary_path(content: bytes, suffix: str) -> Iterator[str]:
    descriptor, filename = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(content)
        yield filename
    finally:
        Path(filename).unlink(missing_ok=True)


def _docling_text(content: bytes, filename: str) -> str:
    from docling.document_converter import DocumentConverter

    suffix = Path(filename).suffix or ".bin"
    with _temporary_path(content, suffix) as temporary_path:
        converter = DocumentConverter()
        result = converter.convert(temporary_path)
        text = result.document.export_to_markdown()
        del result, converter
        gc.collect()
        return text


def _text_from_payload(
    content: bytes,
    filename: str,
    processor: str = "auto",
) -> tuple[str, str, list[dict[str, str]]]:
    attempts: list[dict[str, str]] = []
    lower = filename.lower()
    if lower.endswith(".json"):
        return content.decode("utf-8"), "json", [{"backend": "json", "outcome": "success"}]
    docling_attempted = False
    rich_document = lower.endswith((".html", ".htm"))
    if processor == "docling" or rich_document:
        docling_attempted = True
        try:
            text = _docling_text(content, filename)
            attempts.append({"backend": "docling", "outcome": "success"})
            return text, "docling", attempts
        except Exception as exc:
            attempts.append(
                {"backend": "docling", "outcome": "failed", "reason": type(exc).__name__}
            )
    if lower.endswith((".txt", ".html", ".htm", ".eml")):
        text = content.decode("utf-8", errors="replace")
        text = unescape(re.sub(r"<[^>]+>", " ", text))
        mode = "html-text" if lower.endswith((".html", ".htm")) else "text"
        attempts.append({"backend": mode, "outcome": "success"})
        return re.sub(r"\s+", " ", text), mode, attempts
    if lower.endswith(".pdf"):
        try:
            import pypdfium2

            document = pypdfium2.PdfDocument(content)
            pages = []
            for page in document:
                text_page = page.get_textpage()
                pages.append(text_page.get_text_range())
            extracted = "\n".join(pages).strip()
            if extracted:
                attempts.append({"backend": "pdf-text", "outcome": "success"})
                return extracted, "pdf-text", attempts
            attempts.append({"backend": "pdf-text", "outcome": "empty"})
        except Exception as exc:
            attempts.append(
                {"backend": "pdf-text", "outcome": "failed", "reason": type(exc).__name__}
            )
    if lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        try:
            import easyocr

            suffix = Path(filename).suffix or ".png"
            with _temporary_path(content, suffix) as temporary_path:
                reader = easyocr.Reader(["en"], gpu=False, verbose=False)
                extracted = "\n".join(reader.readtext(temporary_path, detail=0))
                attempts.append({"backend": "easyocr", "outcome": "success"})
                return extracted, "easyocr", attempts
        except Exception as exc:
            attempts.append(
                {"backend": "easyocr", "outcome": "failed", "reason": type(exc).__name__}
            )
    if not docling_attempted:
        try:
            text = _docling_text(content, filename)
            attempts.append({"backend": "docling", "outcome": "success"})
            return text, "docling", attempts
        except Exception as exc:
            attempts.append(
                {"backend": "docling", "outcome": "failed", "reason": type(exc).__name__}
            )
    if not attempts or attempts[-1].get("backend") != "fallback":
        attempts.append({"backend": "fallback", "outcome": "success"})
    return content.decode("utf-8", errors="replace"), "fallback", attempts


def extract_invoice_from_document(
    document: CanonicalDocument,
    runtime: "AgentRuntime | None" = None,
) -> Invoice:
    text = document.text
    mode = document.processing_mode
    processing_mode = mode
    attempts = list(document.processing_attempts)
    if mode == "json":
        data: dict[str, Any] = json.loads(text)
    else:
        if runtime is not None:
            payload = runtime.extract("ap", text, InvoicePayload)
            data = payload.model_dump(mode="json")
            mode = f"{processing_mode}+ollama-agent"
            attempts.append({"backend": "ollama-agent", "outcome": "success"})
        else:
            data = {}
            for field, pattern in FIELD_PATTERNS.items():
                match = re.search(pattern, text, re.IGNORECASE)
                data[field] = match.group(1) if match else None
            data["lines"] = [match.groupdict() for match in LINE_PATTERN.finditer(text)]

    for optional_amount in ("subtotal", "tax_amount", "freight_amount", "discount_amount"):
        if data.get(optional_amount) is None:
            data.pop(optional_amount, None)

    required = ["vendor_id", "invoice_number", "invoice_date", "currency", "total"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        raise ValueError(f"missing required invoice fields: {', '.join(missing)}")

    payload = InvoicePayload.model_validate(data)
    lines = [
        InvoiceLine(
            line.description,
            line.quantity,
            line.unit_price,
            line.amount,
            line.po_line,
        )
        for line in payload.lines
    ]
    evidence_fields = required + [
        "po_number",
        "subtotal",
        "tax_amount",
        "freight_amount",
        "discount_amount",
    ]
    evidence = {
        field: f"{mode}:{field}"
        for field in evidence_fields
        if field in data and data[field] is not None
    }
    confidence = {
        "json": 1.0,
        "pdf-text+ollama-agent": 0.88,
        "easyocr+ollama-agent": 0.84,
        "docling+ollama-agent": 0.86,
        "docling": 0.82,
        "pdf-text": 0.92,
        "easyocr": 0.80,
    }.get(mode, 0.75)
    return Invoice(
        vendor_id=payload.vendor_id,
        invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        currency=payload.currency,
        total=payload.total,
        po_number=payload.po_number,
        lines=lines,
        source_ref=document.source_ref,
        confidence=confidence,
        evidence=evidence,
        subtotal=payload.subtotal,
        tax_amount=payload.tax_amount,
        freight_amount=payload.freight_amount,
        discount_amount=payload.discount_amount,
        extraction_mode=mode,
        extraction_attempts=attempts,
    )


def extract_invoice(
    content: bytes,
    filename: str,
    source_ref: str,
    processor: str = "auto",
    runtime: "AgentRuntime | None" = None,
) -> Invoice:
    text, mode, attempts = _text_from_payload(content, filename, processor)
    return extract_invoice_from_document(
        CanonicalDocument(
            source_ref=source_ref,
            filename=filename,
            media_type="application/octet-stream",
            text=text,
            processing_mode=mode,
            processing_attempts=attempts,
        ),
        runtime,
    )


def extract_remittance_from_document(
    document: CanonicalDocument,
    runtime: "AgentRuntime | None" = None,
) -> Remittance:
    if document.processing_mode == "json":
        data: dict[str, Any] = json.loads(document.text)
        extraction_mode = "json"
        attempts = list(document.processing_attempts)
    elif runtime is not None:
        agent_payload = runtime.extract("ar", document.text, RemittancePayload)
        data = agent_payload.model_dump(mode="json")
        extraction_mode = f"{document.processing_mode}+ollama-agent"
        attempts = [
            *document.processing_attempts,
            {"backend": "ollama-agent", "outcome": "success"},
        ]
    else:
        data = {}
        for field, pattern in REMITTANCE_PATTERNS.items():
            match = re.search(pattern, document.text, re.IGNORECASE)
            data[field] = match.group(1) if match else None
        extraction_mode = document.processing_mode
        attempts = list(document.processing_attempts)
    required = ["customer_id", "reference", "amount", "currency", "open_item_refs"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        raise ValueError(f"missing required remittance fields: {', '.join(missing)}")
    payload = RemittancePayload.model_validate(data)
    evidence = {field: f"{extraction_mode}:{field}" for field in required}
    confidence = {
        "json": 1.0,
        "pdf-text+ollama-agent": 0.88,
        "easyocr+ollama-agent": 0.84,
        "docling+ollama-agent": 0.86,
        "pdf-text": 0.92,
        "easyocr": 0.80,
    }.get(
        extraction_mode, 0.82
    )
    return Remittance(
        customer_id=payload.customer_id,
        reference=payload.reference,
        amount=payload.amount,
        currency=payload.currency,
        open_item_refs=payload.open_item_refs,
        source_ref=document.source_ref,
        confidence=confidence,
        evidence=evidence,
        extraction_mode=extraction_mode,
        extraction_attempts=attempts,
    )


def extract_remittance(data: dict[str, Any], source_ref: str) -> Remittance:
    payload = RemittancePayload.model_validate(data)
    return Remittance(
        payload.customer_id,
        payload.reference,
        payload.amount,
        payload.currency,
        payload.open_item_refs,
        source_ref,
        confidence=1.0,
        evidence={field: f"structured:{field}" for field in RemittancePayload.model_fields},
        extraction_mode="structured",
        extraction_attempts=[{"backend": "structured", "outcome": "success"}],
    )
