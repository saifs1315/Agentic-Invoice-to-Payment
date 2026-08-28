from __future__ import annotations

import json
import re
import tempfile
from datetime import date
from decimal import Decimal
from html import unescape
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.domain import Invoice, InvoiceLine, Remittance


FIELD_PATTERNS = {
    "vendor_id": r"(?:vendor(?:[_ ]id)?|supplier)\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_-]+)",
    "invoice_number": r"invoice(?:[_ ](?:number|no))?\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_-]+)",
    "invoice_date": r"(?:invoice[_ ]date|date)\s*[:#]\s*(?:\|\s*)?(\d{4}-\d{2}-\d{2})",
    "po_number": r"(?:po|purchase[_ ]order)(?:[_ ](?:number|no))?\s*[:#]\s*(?:\|\s*)?([A-Za-z0-9_-]+)",
    "currency": r"currency\s*[:#]\s*(?:\|\s*)?([A-Z]{3})",
    "total": r"(?:invoice[_ ]total|total)\s*[:#]\s*(?:\|\s*)?([0-9]+(?:\.[0-9]{1,2})?)",
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
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    amount: Decimal = Field(ge=0)
    po_line: int | None = Field(default=None, ge=1)


class InvoicePayload(BaseModel):
    vendor_id: str = Field(min_length=2)
    invoice_number: str = Field(min_length=1)
    invoice_date: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    total: Decimal = Field(gt=0)
    po_number: str | None = None
    lines: list[InvoiceLinePayload] = Field(default_factory=list)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> str:
        return str(value).upper()


def _text_from_payload(content: bytes, filename: str) -> tuple[str, str]:
    lower = filename.lower()
    if lower.endswith(".json"):
        return content.decode("utf-8"), "json"
    if lower.endswith((".txt", ".html", ".htm", ".eml")):
        text = content.decode("utf-8", errors="replace")
        text = unescape(re.sub(r"<[^>]+>", " ", text))
        return re.sub(r"\s+", " ", text), "text"
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
                return extracted, "pdf-text"
        except Exception:
            pass
    if lower.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        try:
            import easyocr

            suffix = Path(filename).suffix or ".png"
            with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
                temporary.write(content)
                temporary.flush()
                reader = easyocr.Reader(["en"], gpu=False, verbose=False)
                return "\n".join(reader.readtext(temporary.name, detail=0)), "easyocr"
        except Exception:
            pass
    try:
        from docling.document_converter import DocumentConverter

        suffix = Path(filename).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
            temporary.write(content)
            temporary.flush()
            result = DocumentConverter().convert(temporary.name)
            return result.document.export_to_markdown(), "docling"
    except Exception:
        return content.decode("utf-8", errors="replace"), "fallback"


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


def extract_invoice(content: bytes, filename: str, source_ref: str) -> Invoice:
    text, mode = _text_from_payload(content, filename)
    if mode == "json":
        data: dict[str, Any] = json.loads(text)
    else:
        data = {}
        if settings.llm_extraction_enabled:
            try:
                data = _ollama_extract(text)
                mode = "ollama"
            except Exception:
                data = {}
        if not data:
            for field, pattern in FIELD_PATTERNS.items():
                match = re.search(pattern, text, re.IGNORECASE)
                data[field] = match.group(1) if match else None
            data["lines"] = [match.groupdict() for match in LINE_PATTERN.finditer(text)]

    required = ["vendor_id", "invoice_number", "invoice_date", "currency", "total"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        raise ValueError(f"missing required invoice fields: {', '.join(missing)}")

    if not data.get("lines"):
        data["lines"] = [
            {
                "description": "Invoice total",
                "quantity": "1",
                "unit_price": data["total"],
                "amount": data["total"],
                "po_line": 1,
            }
        ]
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
    evidence = {field: f"{mode}:{field}" for field in required + ["po_number"] if data.get(field)}
    confidence = {
        "json": 1.0,
        "ollama": 0.88,
        "docling": 0.82,
        "pdf-text": 0.92,
        "easyocr": 0.80,
    }.get(mode, 0.75)
    return Invoice(
        payload.vendor_id,
        payload.invoice_number,
        payload.invoice_date,
        payload.currency,
        payload.total,
        payload.po_number,
        lines,
        source_ref,
        confidence=confidence,
        evidence=evidence,
    )


def extract_remittance(data: dict[str, Any], source_ref: str) -> Remittance:
    return Remittance(str(data["customer_id"]), str(data["reference"]), Decimal(str(data["amount"])), str(data.get("currency", "USD")), [str(x) for x in data.get("open_item_refs", [])], source_ref)
