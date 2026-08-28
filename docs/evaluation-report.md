# Evaluation report

## Executive summary

LedgerPilot passed all 11 automated unit/workflow tests and produced the expected extraction, match, exception, routing, and posting decisions on a six-document synthetic benchmark spanning JSON, text-native PDF, and scan-like PNG. The controlled set scored 100% for labeled field extraction, match decisions, exception classification, exception routing recall, and audit-chain integrity. No ineligible invoice was auto-posted.

These results demonstrate implementation correctness against known fixtures. They do not estimate production accuracy across diverse vendor layouts, scans, languages, handwriting, or adversarial documents.

## Dataset

| Format | Cases | Expected behavior |
|---|---:|---|
| JSON | 3 | Clean PO match, 10% price variance, and missing PO |
| Text-native PDF | 2 | Clean PO match and 10% price variance |
| Scan-like PNG | 1 | OCR five core fields and one line, then clean PO match |

Labels are stored in `evaluation/dataset.json`; documents are in `evaluation/fixtures/`. The PDFs and PNG are generated deterministically by `evaluation/generate_fixtures.py` and contain no real financial data.

## Pipeline under test

- JSON uses strict Pydantic validation.
- Text-native PDFs use local PDF text extraction first; Docling remains the rich-layout fallback.
- Images use local EasyOCR; Docling is the fallback when a direct OCR path is unavailable.
- Extracted payloads are validated against the same typed schema before finance logic runs.
- LangGraph retrieves policy context, runs deterministic matching, and conditionally routes to automatic posting or human review.

## Metric definitions

- **Field-level extraction accuracy:** correct labeled core fields divided by all labeled core fields.
- **Document decision accuracy:** documents whose match/no-match decision equals the label.
- **Exception-classification accuracy:** documents containing the labeled exception code, or no variance for a clean case.
- **Exception-routing recall:** labeled exception cases sent to human review.
- **STP rate:** documents posted without human intervention divided by all documents received.
- **False auto-post rate:** ineligible documents automatically posted divided by all documents.
- **Audit-chain integrity:** workflows whose recomputed SHA-256 links remain valid.
- **RAGAS:** reserved for a production policy-retrieval dataset with question/context/answer labels; it is not applied to deterministic numeric matching.

## Results

| Metric | Result |
|---|---:|
| Field-level extraction accuracy | 100.00% |
| Match-decision accuracy | 100.00% |
| Exception-classification accuracy | 100.00% |
| Exception-routing recall | 100.00% |
| Straight-through-processing rate | 50.00% |
| False auto-post rate | 0.00% |
| Mean extraction confidence | 94.00% |
| Audit-chain integrity | 100.00% |

Per-format field and decision accuracy were 100% for JSON (3), PDF (2), and PNG (1). The result is reproducible with `python evaluation/run_evaluation.py`; machine-readable evidence is checked in at `evaluation/results/latest.json`.

## Automated verification

Eleven tests cover typed extraction validation, text line parsing, clean and boundary matching, out-of-tolerance price, receipt shortfall, missing PO, duplicate detection, durable workflow state, idempotent posting, exact AR cash application, and partial-payment escalation.

## Production pilot plan

1. Sample at least 500 permissioned invoices across top vendors and a long-tail stratum.
2. Label header and line fields with dual review and adjudication.
3. Report precision/recall or exact accuracy per field, weighted and macro-averaged by vendor/template.
4. Stratify results by digital PDF, scan quality, language, page count, and line count.
5. Measure false-STP rate separately and set a safety-first go-live threshold.
6. Evaluate duplicate recall, exception routing precision, journal reconciliation, and time-to-resolution.
7. For policy RAG, create question/context/answer triples and use RAGAS context precision, context recall, and faithfulness alongside human review.
8. Run prompt-injection, altered-bank-detail, malformed-file, oversized-file, and adversarial OCR tests.

## Limitations

- Six synthetic documents are too few for confidence intervals or vendor-level generalization.
- The scan is controlled and English-only; it does not represent mobile photos or degraded fax inputs.
- The ERP is a mock adapter; no sandbox API latency or authentication was measured.
- Human-review timing and reviewer agreement are not measured.
- Ollama extraction and Phoenix tracing remain optional profiles and require their services to be started.
