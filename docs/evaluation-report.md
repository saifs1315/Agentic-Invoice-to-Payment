# Evaluation report

## Executive summary

The prototype passed all nine automated unit/workflow tests and produced the expected decisions on a three-document synthetic benchmark. Field-level extraction accuracy, match-decision accuracy, exception classification, and audit-chain integrity were 100% on this controlled set. Straight-through processing was 33.33% because only one of the three invoices was intentionally eligible for automatic posting.

These results demonstrate implementation correctness against known fixtures. They do not estimate production accuracy across diverse vendor layouts, scans, languages, handwriting, or adversarial documents.

## Dataset

The versioned dataset contains:

| Case | Expected behavior |
|---|---|
| Clean PO-1001 invoice | Extract five core fields, pass three-way match, post journal |
| PO-1001 with 10% price increase | Detect `PRICE_VARIANCE` and `TOTAL_VARIANCE`, route to exception |
| Invoice without PO | Detect `MISSING_PO`, route to exception |

Labels are stored in `evaluation/dataset.json`; documents are in `evaluation/fixtures/`. JSON is used to isolate workflow correctness from OCR variability.

## Metric definitions

- **Field-level extraction accuracy:** correct labeled core fields divided by all labeled core fields.
- **Document exact-match rate:** documents whose match/no-match decision equals the expected decision divided by documents evaluated.
- **Exception-classification accuracy:** documents whose expected exception code is present, or whose clean case contains no variance.
- **STP rate:** documents posted without human intervention divided by all documents received.
- **Audit-chain integrity:** workflows for which recomputed SHA-256 links remain valid.
- **RAGAS:** applicable to policy retrieval faithfulness/relevance experiments, not to deterministic numeric matching. It is included as an optional dependency but is not misapplied to the three JSON fixtures.

## Results

| Metric | Numerator / denominator | Result |
|---|---:|---:|
| Field-level extraction accuracy | 15 / 15 | 100.00% |
| Match-decision accuracy | 3 / 3 | 100.00% |
| Exception-classification accuracy | 3 / 3 | 100.00% |
| Straight-through-processing rate | 1 / 3 | 33.33% |
| Audit-chain integrity | 3 / 3 | 100.00% |

The result is reproducible with `python evaluation/run_evaluation.py`. The latest machine-readable output is written to `evaluation/results/latest.json` and excluded from commits so executions do not create noise.

## Automated verification

Nine tests cover clean and boundary matching, out-of-tolerance price, receipt shortfall, missing PO, duplicate detection, idempotent posting, exact AR cash application, and partial-payment escalation.

## Production pilot plan

1. Sample at least 500 permissioned invoices across the top vendors and a long-tail stratum.
2. Label header and line fields with dual review and adjudication.
3. Report precision/recall or exact accuracy per field, weighted and macro-averaged by vendor/template.
4. Stratify results by digital PDF, scan quality, language, page count, and line count.
5. Measure false-STP rate separately; the target should favor safety over volume.
6. Evaluate duplicate recall, exception routing precision, journal reconciliation, and time-to-resolution.
7. For policy RAG, create question/context/answer triples and use RAGAS context precision, context recall, and faithfulness alongside human review.
8. Run prompt-injection, altered-bank-detail, malformed-file, oversized-file, and adversarial OCR tests.

## Limitations

- The synthetic benchmark is too small for confidence intervals.
- JSON fixtures produce perfect extraction and do not exercise Docling OCR.
- The ERP is a mock adapter; no sandbox API latency or authentication was measured.
- Human-review timing and reviewer agreement are not measured.
- The optional Ollama and Phoenix services were designed into the stack but require the full Docker profile to exercise.

