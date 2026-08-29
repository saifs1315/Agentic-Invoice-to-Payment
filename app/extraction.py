from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from html import unescape
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.domain import MAX_MONETARY_AMOUNT, MAX_QUANTITY, Invoice, InvoiceLine, Remittance


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
        result = DocumentConverter().convert(temporary_path)
        return result.document.export_to_markdown()


def _text_from_payload(
    content: bytes,
    filename: str,
    processor: str = "auto",
) -> tuple[str, str, list[dict[str, str]]]:
    attempts: list[dict[str, str]] = []
    lower = filename.lower()
    if lower.endswith(".json"):
        return content.decode("utf-8"), "json", [{"backend": "json", "outcome": "success"}]
    if processor == "docling":
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
    try:
        text = _docling_text(content, filename)
        attempts.append({"backend": "docling", "outcome": "success"})
        return text, "docling", attempts
    except Exception as exc:
        attempts.append(
            {"backend": "docling", "outcome": "failed", "reason": type(exc).__name__}
        )
        attempts.append({"backend": "fallback", "outcome": "success"})
        return content.decode("utf-8", errors="replace"), "fallback", attempts


def _ollama_extract(text: str) -> dict[str, Any]:
    from ollama import Client

    prompt = (
        "Extract the invoice into the supplied JSON schema. Use null for a missing PO number. "
        "Never infer identifiers or amounts that are not in the document. Return JSON only.\n\n"
        + text[:30000]
    )
    response = Client(host=settings.ollama_base_url).chat(
        model=settings.ollama_model,
        messages=[{"role": "user", "content": prompt}],
        format=InvoicePayload.model_json_schema(),
        options={"temperature": 0},
    )
    if isinstance(response, dict):
        content = response["message"]["content"]
    else:
        content = response.message.content
    return json.loads(content)


def extract_invoice(
    content: bytes,
    filename: str,
    source_ref: str,
    processor: str = "auto",
) -> Invoice:
    text, mode, attempts = _text_from_payload(content, filename, processor)
    if mode == "json":
        data: dict[str, Any] = json.loads(text)
    else:
        data = {}
        if settings.llm_extraction_enabled:
            try:
                data = _ollama_extract(text)
                mode = "ollama"
                attempts.append({"backend": "ollama", "outcome": "success"})
            except Exception as exc:
                attempts.append(
                    {"backend": "ollama", "outcome": "failed", "reason": type(exc).__name__}
                )
                data = {}
        if not data:
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
        "ollama": 0.88,
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
        source_ref=source_ref,
        confidence=confidence,
        evidence=evidence,
        subtotal=payload.subtotal,
        tax_amount=payload.tax_amount,
        freight_amount=payload.freight_amount,
        discount_amount=payload.discount_amount,
        extraction_mode=mode,
        extraction_attempts=attempts,
    )


def extract_remittance(data: dict[str, Any], source_ref: str) -> Remittance:
    return Remittance(str(data["customer_id"]), str(data["reference"]), Decimal(str(data["amount"])), str(data.get("currency", "USD")), [str(x) for x in data.get("open_item_refs", [])], source_ref)
