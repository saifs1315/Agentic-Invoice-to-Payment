from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.audit import AuditLedger
from app.config import Settings
from app.erp import MockERP
from app.extraction import extract_invoice
from app.repository import MemoryRepository
from app.workflow import InvoiceWorkflow


ROOT = Path(__file__).resolve().parent


def evaluate() -> dict:
    dataset = json.loads((ROOT / "dataset.json").read_text(encoding="utf-8"))
    field_hits = field_total = match_hits = exception_hits = posted = 0
    for item in dataset:
        repo, audit, erp = MemoryRepository(), AuditLedger(), MockERP()
        workflow = InvoiceWorkflow(repo, audit, erp, Settings())
        path = ROOT / item["file"]
        invoice = extract_invoice(path.read_bytes(), path.name, f"fixture:{path.name}")
        workflow.ingest(invoice)
        actual = invoice.to_dict()
        for field, expected in item["expected_fields"].items():
            field_total += 1
            field_hits += actual.get(field) == expected
        outcome = workflow.match(invoice.id)
        result = outcome["result"]
        match_hits += result["matched"] == item["expected_match"]
        codes = {v["code"] for v in result["variances"]}
        exception_hits += (item["expected_exception"] is None and not codes) or item["expected_exception"] in codes
        if result["matched"]:
            workflow.post(invoice.id, f"eval:{invoice.id}")
            posted += 1
        assert audit.verify()

    count = len(dataset)
    metrics = {
        "dataset_size": count,
        "field_level_extraction_accuracy": round(field_hits / field_total, 4),
        "document_exact_match_rate": round(match_hits / count, 4),
        "exception_classification_accuracy": round(exception_hits / count, 4),
        "straight_through_processing_rate": round(posted / count, 4),
        "audit_chain_integrity_rate": 1.0,
        "notes": "Synthetic deterministic benchmark; not representative of production document diversity. RAGAS is reserved for policy-retrieval experiments because matching is rule-based."
    }
    output = ROOT / "results" / "latest.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2))
