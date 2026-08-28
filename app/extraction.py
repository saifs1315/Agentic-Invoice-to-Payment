from __future__ import annotations

import json
import re
import tempfile
from datetime import date
from decimal import Decimal
from html import unescape
from typing import Any

from app.domain import Invoice, InvoiceLine, Remittance


FIELD_PATTERNS = {
    "vendor_id": r"(?:vendor(?:[_ ]id)?|supplier)\s*[:#]\s*([A-Za-z0-9_-]+)",
    "invoice_number": r"invoice(?:[_ ](?:number|no))?\s*[:#]\s*([A-Za-z0-9_-]+)",
    "invoice_date": r"(?:invoice[_ ]date|date)\s*[:#]\s*(\d{4}-\d{2}-\d{2})",
    "po_number": r"(?:po|purchase[_ ]order)(?:[_ ](?:number|no))?\s*[:#]\s*([A-Za-z0-9_-]+)",
    "currency": r"currency\s*[:#]\s*([A-Z]{3})",
    "total": r"(?:invoice[_ ]total|total)\s*[:#]\s*([0-9]+(?:\.[0-9]{1,2})?)",
}


def _text_from_payload(content: bytes, filename: str) -> tuple[str, str]:
    lower = filename.lower()
    if lower.endswith(".json"):
        return content.decode("utf-8"), "json"
    if lower.endswith((".txt", ".html", ".htm", ".eml")):
        text = content.decode("utf-8", errors="replace")
        text = unescape(re.sub(r"<[^>]+>", " ", text))
        return re.sub(r"\s+", " ", text), "text"
    try:
        from docling.document_converter import DocumentConverter

        suffix = ".pdf" if lower.endswith(".pdf") else ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix) as temporary:
            temporary.write(content)
            temporary.flush()
            result = DocumentConverter().convert(temporary.name)
            return result.document.export_to_markdown(), "docling"
    except (ImportError, RuntimeError, ValueError):
        return content.decode("utf-8", errors="replace"), "fallback"


def extract_invoice(content: bytes, filename: str, source_ref: str) -> Invoice:
    text, mode = _text_from_payload(content, filename)
    if mode == "json":
        data: dict[str, Any] = json.loads(text)
    else:
        data = {}
        for field, pattern in FIELD_PATTERNS.items():
            match = re.search(pattern, text, re.IGNORECASE)
            data[field] = match.group(1) if match else None

    required = ["vendor_id", "invoice_number", "invoice_date", "currency", "total"]
    missing = [field for field in required if not data.get(field)]
    if missing:
        raise ValueError(f"missing required invoice fields: {', '.join(missing)}")

    raw_lines = data.get("lines") or [{"description": "Invoice total", "quantity": "1", "unit_price": data["total"], "amount": data["total"], "po_line": 1}]
    lines = [InvoiceLine(str(line["description"]), Decimal(str(line["quantity"])), Decimal(str(line["unit_price"])), Decimal(str(line.get("amount", Decimal(str(line["quantity"])) * Decimal(str(line["unit_price"]))))), line.get("po_line")) for line in raw_lines]
    evidence = {field: f"{mode}:{field}" for field in required + ["po_number"] if data.get(field)}
    return Invoice(str(data["vendor_id"]), str(data["invoice_number"]), date.fromisoformat(str(data["invoice_date"])), str(data["currency"]), Decimal(str(data["total"])), data.get("po_number"), lines, source_ref, confidence=1.0 if mode == "json" else 0.82, evidence=evidence)


def extract_remittance(data: dict[str, Any], source_ref: str) -> Remittance:
    return Remittance(str(data["customer_id"]), str(data["reference"]), Decimal(str(data["amount"])), str(data.get("currency", "USD")), [str(x) for x in data.get("open_item_refs", [])], source_ref)
