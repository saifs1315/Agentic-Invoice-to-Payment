# Evaluation report

## Executive summary

LedgerPilot passed all 21 automated unit, API, control, workflow, context, and audit tests and produced the expected extraction, match, exception, routing, and posting decisions on a seven-document synthetic benchmark spanning JSON, text-native PDF, scan-like PNG, and a forced-Docling HTML case. The controlled set scored 100% for evaluation coverage, labeled field extraction, match decisions, exception classification, exception routing recall, and audit-chain integrity. No ineligible invoice was auto-posted. A separate four-query policy dataset scored 100% RAGAS non-LLM context precision and recall.

These results demonstrate implementation correctness against known fixtures. They do not estimate production accuracy across diverse vendor layouts, scans, languages, handwriting, or adversarial documents.

## Dataset

| Format | Cases | Expected behavior |
|---|---:|---|
| JSON | 3 | Clean PO match, 10% price variance, and missing PO |
| Text-native PDF | 2 | Clean PO match and 10% price variance |
| Scan-like PNG | 1 | OCR five core fields and one line, then clean PO match |
| HTML through Docling | 1 | Force Docling conversion, extract fields, then clean PO match |

Labels are stored in `evaluation/dataset.json`; documents are in `evaluation/fixtures/`. The PDFs and PNG are generated deterministically by `evaluation/generate_fixtures.py` and contain no real financial data.

## Pipeline under test

- JSON uses strict Pydantic validation.
- Text-native PDFs use local PDF text extraction first; Docling remains the rich-layout fallback.
- Images use local EasyOCR; Docling is the fallback when a direct OCR path is unavailable. The HTML fixture explicitly forces Docling so that path is measured in every full evaluation.
- Extracted payloads are validated against the same typed schema before finance logic runs.
- Extraction records the selected backend and the success/failure type of each attempted backend.
- LangGraph retrieves policy context, runs deterministic matching, and conditionally routes to automatic posting or human review.
- Policy retrieval uses a real LlamaIndex `VectorStoreIndex` fused with repository/pgvector ranking.

## Metric definitions

- **Field-level extraction accuracy:** correct labeled core fields divided by all labeled core fields.
- **Document decision accuracy:** documents whose match/no-match decision equals the label.
- **Exception-classification accuracy:** documents containing the labeled exception code, or no variance for a clean case.
- **Exception-routing recall:** labeled exception cases sent to human review.
- **STP rate:** documents posted without human intervention divided by all documents received.
- **False auto-post rate:** ineligible documents automatically posted divided by all documents.
- **Audit-chain integrity:** workflows whose recomputed SHA-256 links remain valid.
- **Evaluation coverage:** documents completing extraction and workflow evaluation divided by all selected documents. Any failure makes the command exit non-zero.
- **RAGAS context precision/recall:** non-LLM similarity metrics over four labeled finance-policy retrieval cases. RAGAS is applied to retrieval, not to deterministic numeric matching.

## Results

| Metric | Result |
|---|---:|
| Field-level extraction accuracy | 100.00% |
| Match-decision accuracy | 100.00% |
| Exception-classification accuracy | 100.00% |
| Exception-routing recall | 100.00% |
| Evaluation coverage | 100.00% |
| Straight-through-processing rate | 57.14% |
| False auto-post rate | 0.00% |
| Mean extraction confidence | 92.29% |
| Audit-chain integrity | 100.00% |
| RAGAS non-LLM context precision | 100.00% |
| RAGAS non-LLM context recall | 100.00% |

Per-format field and decision accuracy were 100% for JSON (3), PDF (2), PNG (1), and HTML/Docling (1). Extraction modes were JSON (3), PDF text (2), EasyOCR (1), and Docling (1). Document results are reproducible with `python evaluation/run_evaluation.py`; RAGAS results use `python evaluation/run_rag_evaluation.py`. Machine-readable evidence is checked in under `evaluation/results/`.

## Automated verification

Twenty-one tests cover typed extraction and field evidence, cross-platform temporary files, API contracts, clean and boundary matching, partial invoices, bounded ancillary charges, monetary magnitude limits, missing-line routing, line and total arithmetic, out-of-tolerance price, receipt shortfall, missing PO, duplicate detection, human approval gating, durable workflow state, idempotent posting, LlamaIndex retrieval, audit tamper detection, exact AR cash application, and partial-payment escalation.

## Production pilot plan

1. Sample at least 500 permissioned invoices across top vendors and a long-tail stratum.
2. Label header and line fields with dual review and adjudication.
3. Report precision/recall or exact accuracy per field, weighted and macro-averaged by vendor/template.
4. Stratify results by digital PDF, scan quality, language, page count, and line count.
5. Measure false-STP rate separately and set a safety-first go-live threshold.
6. Evaluate duplicate recall, exception routing precision, journal reconciliation, and time-to-resolution.
7. Expand the four-case retrieval set with production policy chunks and add LLM-based faithfulness only when a governed evaluator model is available; retain the current deterministic RAGAS precision/recall gate.
8. Run prompt-injection, altered-bank-detail, malformed-file, oversized-file, and adversarial OCR tests.

## Limitations

- Seven synthetic documents are too few for confidence intervals or vendor-level generalization.
- The scan is controlled and English-only; it does not represent mobile photos or degraded fax inputs.
- Fixtures are intentionally authored in parser-friendly layouts; the 100% result demonstrates regression correctness, not template generalization.
- The ERP is a mock adapter; no sandbox API latency or authentication was measured.
- Human-review timing and reviewer agreement are not measured.
- Ollama extraction and Phoenix tracing remain optional profiles and require their services to be started.
- Cumulative prior invoicing per PO line is not modeled; partial invoices are checked against ordered and received upper bounds in the current transaction only.
