# Evaluation report

## Executive summary

LedgerPilot passed all 66 automated unit, API, runtime, control, workflow, context, and audit tests. The mandatory live-agent AP benchmark covers seven synthetic documents; the mirrored AR benchmark covers nine documents across JSON, text, PDF, PNG, and HTML. AP scored 88.57% labeled field extraction and AR scored 97.22%; both scored 100% evaluation coverage, finance decisions, exception classification, and audit-chain integrity. No ineligible invoice was auto-posted and no invalid remittance caused cash application. The live agent conservatively escalated 0 of 4 eligible AP cases and 0 of 5 eligible AR cases in the recorded run. A nine-query EmbeddingGemma policy dataset scored 88.89% RAGAS non-LLM context precision and recall.

These results demonstrate implementation correctness against known fixtures. They do not estimate production accuracy across diverse vendor layouts, scans, languages, handwriting, or adversarial documents.

## Dataset

| Format | Cases | Expected behavior |
|---|---:|---|
| JSON | 3 | Clean PO match, 10% price variance, and missing PO |
| Text-native PDF | 2 | Clean PO match and 10% price variance |
| Scan-like PNG | 1 | OCR five core fields and one line, then clean PO match |
| HTML through Docling | 1 | Use default Docling conversion, extract fields, then clean PO match |

The AR set is stored in `evaluation/ar_dataset.json` and contains five structured control cases (clean, currency mismatch, partial, overpayment, missing item) plus clean text, PDF, scan, and HTML remittance advices. Duplicate remittance, correction/re-match, human approval, and replay behavior are covered in deterministic tests because those scenarios require state shared across requests.

Labels are stored in `evaluation/dataset.json`; documents are in `evaluation/fixtures/`. The PDFs and PNG are generated deterministically by `evaluation/generate_fixtures.py` and contain no real financial data.

## Pipeline under test

- JSON uses strict Pydantic validation for document fields; the supervisor and AP/AR action agents remain mandatory.
- HTML/rich-layout documents use Docling as the default canonical-text converter; the AP benchmark no longer relies on a special forced-processor flag to exercise it.
- Text-native PDFs use PDFium and scans use EasyOCR first to fit the 3.5 GB local Docker budget, with Docling retained as their recorded fallback. A load test of Docling-before-OCR for every scan was rejected after an out-of-memory exit; the evaluation records the selected backend and every attempted backend.
- A shared processor produces one canonical document and classifies it as AP, AR, or ambiguous before the domain extractor runs.
- AP invoices and AR remittances use separate typed schemas after classification.
- Extraction records both layers, for example `pdf-text+ollama-agent`, plus every backend attempt and outcome.
- A parent LangGraph dispatches to AP and AR LangGraph subgraphs. Each retrieves policy context, runs deterministic controls, and conditionally routes to HTTP ERP posting or human review.
- Policy retrieval uses a real LlamaIndex `VectorStoreIndex` fused with repository/pgvector ranking.

## Metric definitions

- **Field-level extraction accuracy:** correct labeled core fields divided by all labeled core fields.
- **Document decision accuracy:** documents whose match/no-match decision equals the label.
- **Exception-classification accuracy:** documents containing the labeled exception code, or no variance for a clean case.
- **Exception-routing recall:** labeled exception cases sent to human review.
- **STP rate:** documents posted without human intervention divided by all documents received.
- **Conservative-escalation rate:** otherwise eligible deterministic matches that the bounded action agent sends to human review divided by all eligible auto-action cases. This live-model metric may vary between runs.
- **False auto-post rate:** ineligible documents automatically posted divided by all documents.
- **Audit-chain integrity:** workflows whose recomputed SHA-256 links remain valid.
- **Evaluation coverage:** documents completing extraction and workflow evaluation divided by all selected documents. Any failure makes the command exit non-zero.
- **RAGAS context precision/recall:** non-LLM similarity metrics over nine paraphrased, multi-policy, and out-of-domain finance-policy cases. RAGAS is applied to retrieval, not to deterministic numeric matching.
- **AR document classification:** remittance documents correctly dispatched to the AR subgraph divided by AR documents.
- **False cash-application rate:** ineligible remittances applied to open items divided by all AR documents.

## Results

| Metric | Result |
|---|---:|
| Field-level extraction accuracy | 88.57% |
| Match-decision accuracy | 100.00% |
| Exception-classification accuracy | 100.00% |
| Exception-routing recall | 100.00% |
| Evaluation coverage | 100.00% |
| Straight-through-processing rate | 57.14% |
| Eligible auto-action cases | 4 |
| Conservative-escalation rate | 0.00% (0 / 4) |
| False auto-post rate | 0.00% |
| Mean extraction confidence | 92.29% |
| Audit-chain integrity | 100.00% |
| RAGAS non-LLM context precision | 88.89% |
| RAGAS non-LLM context recall | 88.89% |

### AR results

| Metric | Result |
|---|---:|
| Evaluation coverage | 100.00% |
| Document classification accuracy | 100.00% |
| Field-level extraction accuracy | 97.22% |
| Match-decision accuracy | 100.00% |
| Exception-classification accuracy | 100.00% |
| Eligible auto-action cases | 5 |
| Conservative-escalation rate | 0.00% (0 / 5) |
| False cash-application rate | 0.00% |
| Audit-chain integrity | 100.00% |

AR per-format field accuracy is JSON 100% (5), text 100% (1), PDF 100% (1), HTML/Docling 100% (1), and scan 75% (1). The lower-extraction scan still reached the correct deterministic finance decision; the result is reported rather than hidden because this benchmark is intended to expose extraction risk.

AP field accuracy was JSON 100% (3), PDF 80% (2), PNG 80% (1), and HTML/Docling 80% (1); decision accuracy was 100% for every format. Results are reproducible with `python evaluation/run_evaluation.py`, `python evaluation/run_ar_evaluation.py`, and `python evaluation/run_rag_evaluation.py`. Machine-readable evidence is checked in under `evaluation/results/`.

## Automated verification

Sixty-six tests cover the original AP controls plus mandatory-runtime configuration, schema-constrained agent contracts (including honest omission of unknown confidence), validated policy-query fallback and fail-closed behavior, bounded AP/AR action authority with deterministic vetoes, shared AP/AR classification, canonical extraction, parent-graph dispatch, ambiguous-document escalation, recursion-budget failure handling, conservative action-agent escalation, AR graph transitions, partial-payment correction and full re-match, prohibition on force-approving invalid AR matches, valid AR human approval, cash idempotency, Mock ERP HTTP PO/GR/journal/open-item/cash contracts, terminal AP transition guards (including rejection and approval-state posting controls), audited ERP failures, non-PO override parity, bounded uploads, API dispatch, durable generic workflow state, and the unified exception surface.

## Production pilot plan

1. Sample at least 500 permissioned invoices across top vendors and a long-tail stratum.
2. Label header and line fields with dual review and adjudication.
3. Report precision/recall or exact accuracy per field, weighted and macro-averaged by vendor/template.
4. Stratify results by digital PDF, scan quality, language, page count, and line count.
5. Measure false-STP rate separately and set a safety-first go-live threshold.
6. Evaluate duplicate recall, exception routing precision, journal reconciliation, and time-to-resolution.
7. Expand the nine-case retrieval set with production policy chunks and add LLM-based faithfulness only when a governed evaluator model is available; retain the current deterministic RAGAS precision/recall gate.
8. Run prompt-injection, altered-bank-detail, malformed-file, oversized-file, and adversarial OCR tests.

## Limitations

- Seven AP and nine AR synthetic documents are too few for confidence intervals or counterparty-level generalization.
- The scan is controlled and English-only; it does not represent mobile photos or degraded fax inputs.
- Fixtures are intentionally authored in parser-friendly layouts; the 100% result demonstrates regression correctness, not template generalization.
- The ERP is a separate stateful Mock API over HTTP, not a vendor sandbox. Authentication, vendor-specific schemas, and realistic network latency are not measured.
- Human-review timing and reviewer agreement are not measured.
- Ollama, Qwen, EmbeddingGemma, and Phoenix are mandatory Compose services. The application fails closed when the AI runtime or either model is unavailable.
- EmbeddingGemma retrieved the correct references for all eight finance-policy cases, including the two-policy query, but did not abstain on the out-of-domain meal-reimbursement query. A production pilot needs a larger labeled corpus and a calibrated abstention threshold.
- Cumulative prior invoicing per PO line is not modeled; partial invoices are checked against ordered and received upper bounds in the current transaction only.
