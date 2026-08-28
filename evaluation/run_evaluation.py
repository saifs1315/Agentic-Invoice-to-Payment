from __future__ import annotations

# ruff: noqa: E402 - add the repository root before importing the application package.

import json
import os
import sys
from collections import defaultdict
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
    requested_formats = {
        value.strip().lower().lstrip(".")
        for value in os.getenv("EVALUATION_FORMATS", "").split(",")
        if value.strip()
    }
    if requested_formats:
        dataset = [
            item
            for item in dataset
            if Path(item["file"]).suffix.lower().lstrip(".") in requested_formats
        ]
    field_hits = field_total = match_hits = exception_hits = posted = 0
    audit_valid = false_auto_posts = exception_cases = exception_routed = 0
    confidences: list[float] = []
    by_format: dict[str, dict[str, int]] = defaultdict(
        lambda: {"documents": 0, "field_hits": 0, "field_total": 0, "decision_hits": 0}
    )
    failures: list[dict[str, str]] = []

    for item in dataset:
        path = ROOT / item["file"]
        extension = path.suffix.lower().lstrip(".")
        bucket = by_format[extension]
        bucket["documents"] += 1
        bucket["field_total"] += len(item["expected_fields"])
        field_total += len(item["expected_fields"])
        if item["expected_exception"] is not None:
            exception_cases += 1

        repo, audit, erp = MemoryRepository(), AuditLedger(), MockERP()
        workflow = InvoiceWorkflow(repo, audit, erp, Settings())
        try:
            invoice = extract_invoice(path.read_bytes(), path.name, f"fixture:{path.name}")
            workflow.ingest(invoice)
            actual = invoice.to_dict()
            confidences.append(invoice.confidence)
            for field, expected in item["expected_fields"].items():
                hit = actual.get(field) == expected
                field_hits += hit
                bucket["field_hits"] += hit

            outcome = workflow.match(invoice.id)
            result = outcome["result"]
            decision_hit = result["matched"] == item["expected_match"]
            match_hits += decision_hit
            bucket["decision_hits"] += decision_hit
            codes = {variance["code"] for variance in result["variances"]}
            exception_hit = (
                item["expected_exception"] is None and not codes
            ) or item["expected_exception"] in codes
            exception_hits += exception_hit
            exception_routed += item["expected_exception"] is not None and outcome["next_action"] == "human-review"
            posted += outcome["next_action"] == "posted"
            false_auto_posts += (not item["expected_match"]) and outcome["next_action"] == "posted"
            audit_valid += audit.verify()
        except Exception as exc:
            failures.append({"file": item["file"], "error": f"{type(exc).__name__}: {exc}"})

    count = len(dataset)
    if count == 0:
        raise ValueError("EVALUATION_FORMATS did not select any dataset items")
    format_metrics = {
        extension: {
            "documents": values["documents"],
            "field_accuracy": round(values["field_hits"] / values["field_total"], 4),
            "decision_accuracy": round(values["decision_hits"] / values["documents"], 4),
        }
        for extension, values in sorted(by_format.items())
    }
    metrics = {
        "dataset_size": count,
        "formats": format_metrics,
        "field_level_extraction_accuracy": round(field_hits / field_total, 4),
        "document_exact_match_rate": round(match_hits / count, 4),
        "exception_classification_accuracy": round(exception_hits / count, 4),
        "exception_routing_recall": round(exception_routed / exception_cases, 4),
        "straight_through_processing_rate": round(posted / count, 4),
        "false_auto_post_rate": round(false_auto_posts / count, 4),
        "mean_extraction_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
        "audit_chain_integrity_rate": round(audit_valid / count, 4),
        "failed_documents": failures,
        "notes": (
            "Synthetic six-document benchmark spanning JSON, text-native PDF, and scan-like PNG. "
            "Finance decisions remain deterministic; production validation requires permissioned vendor samples."
        ),
    }
    output = ROOT / "results" / "latest.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2))
