from __future__ import annotations

# ruff: noqa: E402 - add the repository root before importing the application package.

import hashlib
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ar_workflow import RemittanceWorkflow
from app.agent_runtime import create_agent_runtime
from app.audit import AuditLedger
from app.config import Settings
from app.context import POLICY_QUERIES
from app.document_processing import UnifiedDocumentProcessor
from app.domain import DocumentKind, SourceEnvelope
from app.erp import MockERP
from app.extraction import extract_remittance_from_document
from app.repository import MemoryRepository


ROOT = Path(__file__).resolve().parent


def evaluate() -> dict:
    config = Settings()
    runtime = create_agent_runtime(config)
    MemoryRepository(runtime.embed)
    runtime.embed(POLICY_QUERIES["ar"])
    dataset = json.loads((ROOT / "ar_dataset.json").read_text(encoding="utf-8"))
    requested_files = {
        value.strip().replace("\\", "/")
        for value in os.getenv("EVALUATION_FILES", "").split(",")
        if value.strip()
    }
    if requested_files:
        dataset = [
            item
            for item in dataset
            if item["file"] in requested_files or Path(item["file"]).name in requested_files
        ]
    if not dataset:
        raise ValueError("evaluation filters did not select any AR dataset items")
    field_hits = field_total = classification_hits = match_hits = exception_hits = 0
    false_cash_applications = audit_valid = 0
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
        repo, audit, erp = MemoryRepository(runtime.embed), AuditLedger(), MockERP()
        workflow = RemittanceWorkflow(repo, audit, erp, config, runtime)
        try:
            content = path.read_bytes()
            envelope = SourceEnvelope(
                content=content,
                filename=path.name,
                media_type="application/octet-stream",
                source_ref=f"fixture:{path.name}",
                content_sha256=hashlib.sha256(content).hexdigest(),
            )
            document = UnifiedDocumentProcessor().process(envelope)
            classification_hits += document.kind == DocumentKind.AR_REMITTANCE
            remittance = extract_remittance_from_document(document, runtime)
            actual = remittance.to_dict()
            for field, expected in item["expected_fields"].items():
                hit = actual.get(field) == expected
                field_hits += hit
                bucket["field_hits"] += hit
            outcome = workflow.ingest(remittance, run=True)
            result = outcome["result"]
            decision_hit = result["matched"] == item["expected_match"]
            match_hits += decision_hit
            bucket["decision_hits"] += decision_hit
            codes = {variance["code"] for variance in result["variances"]}
            exception_hits += (
                item["expected_exception"] is None and not codes
            ) or item["expected_exception"] in codes
            false_cash_applications += (not item["expected_match"]) and result["applied"]
            audit_valid += audit.verify()
        except Exception as exc:
            failures.append({"file": item["file"], "error": f"{type(exc).__name__}: {exc}"})

    count = len(dataset)
    metrics = {
        "dataset_size": count,
        "evaluation_coverage": round((count - len(failures)) / count, 4),
        "document_classification_accuracy": round(classification_hits / count, 4),
        "field_level_extraction_accuracy": round(field_hits / field_total, 4),
        "match_decision_accuracy": round(match_hits / count, 4),
        "exception_classification_accuracy": round(exception_hits / count, 4),
        "false_cash_application_rate": round(false_cash_applications / count, 4),
        "audit_chain_integrity_rate": round(audit_valid / count, 4),
        "formats": {
            extension: {
                "documents": values["documents"],
                "field_accuracy": round(values["field_hits"] / values["field_total"], 4),
                "decision_accuracy": round(values["decision_hits"] / values["documents"], 4),
            }
            for extension, values in sorted(by_format.items())
        },
        "failed_documents": failures,
        "notes": (
            "Synthetic live-agent AR benchmark with deterministic matching and posting guards; "
            "production validation requires permissioned bank advice."
        ),
    }
    output = ROOT / "results" / "ar-latest.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


if __name__ == "__main__":
    result = evaluate()
    print(json.dumps(result, indent=2))
    if result["failed_documents"]:
        raise SystemExit(1)
