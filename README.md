# LedgerPilot

LedgerPilot is an auditable agentic-finance prototype for accounts-payable invoices and accounts-receivable remittances. A deterministic document-processing tool converts mailbox or API attachments into canonical text, candidate fields, provenance, and confidence. A mandatory local supervisor agent then classifies the evidence and dispatches an AP or AR agent. AP performs two-way/three-way matching and posts a Payment Journal, while AR mirrors the same observe-retrieve-match-resolve/post pattern against open items and applies cash. Both use a separate stateful Mock ERP HTTP API.

AI is a runtime requirement, not an optional enhancement. Ollama runs `qwen3.5:2b-q4_K_M` for schema-constrained extraction, supervisor routing, and bounded AP/AR action selection; `embeddinggemma` powers LlamaIndex and PostgreSQL/pgvector policy retrieval. Probabilistic decisions are never allowed to weaken financial controls: Pydantic schemas, per-stage action allow-lists, fixed LangGraph transitions, arithmetic and tolerance checks, approval state, idempotency, and final posting guards remain deterministic and authoritative. If either model is unavailable, finance execution fails closed with `503`.

## What is included

- Microsoft Graph shared-mailbox adapter plus unified PDF, image, HTML, text, and JSON ingestion at `POST /api/v1/ingest-document`.
- One deterministic canonical document tool shared by both domains; it produces text, candidate evidence, confidence, and backend-attempt traces but performs no business action.
- A mandatory Qwen 3.5 supervisor agent and independently bounded AP and AR agents, orchestrated with parent and domain LangGraphs.
- Genuine semantic RAG through `embeddinggemma`, LlamaIndex `VectorStoreIndex`, and PostgreSQL/pgvector ranking; deterministic hash embeddings exist only in an explicitly test-only runtime.
- Two-way and three-way PO/GR matching with configurable tolerances, partial-invoice support, and bounded tax/freight/discount reconciliation.
- Exception detection for arithmetic mismatches, missing PO/line, duplicate invoice, vendor/currency mismatch, price/quantity/total variance, and receipt shortfall.
- Unified AP/AR exception desk plus domain-specific, audited human actions.
- Separate FastAPI Mock ERP service with PO, Goods Receipt, Payment Journal, open-AR-item, and cash-application endpoints; application containers use an HTTP client boundary.
- Mirrored AR document extraction, policy retrieval, deterministic allocation, review/correction/re-match, and idempotent cash application. Invalid matches cannot be force-approved.
- SHA-256 hash-chained tool-call, agent-decision, control, human, and ERP audit events plus PostgreSQL persistence in Compose.
- Durable PostgreSQL workflow state, journals, remittances, and policy embeddings.
- PostgreSQL 16 + pgvector schema, Ollama, and Arize Phoenix services.
- OpenAPI contract, automated tests, synthetic evaluation harness, architecture diagrams, and presentation deck.

## Architecture

![LedgerPilot architecture](docs/diagrams/architecture.svg)

The editable Mermaid sources and a detailed explanation are in [docs/architecture.md](docs/architecture.md). The implementation-to-requirement mapping is in [docs/traceability-matrix.md](docs/traceability-matrix.md).

## Quick start: Docker Compose

Prerequisites: Docker Desktop with Compose v2. The default model pair is tuned for the tested 4 GB GPU / constrained Docker environment: a 1.9 GB Q4 Qwen model plus a 622 MB embedding model, one loaded model at a time, with an 8K context cap. Give Docker at least 4 GB when possible; first startup downloads the Ollama image and both models.

```powershell
Copy-Item .env.example .env
docker compose up --build -d
Invoke-RestMethod http://localhost:8000/api/v1/health
Invoke-RestMethod http://localhost:8080/erp/v1/health
```

The model initializer blocks API startup until both models are pulled. The health response reports the exact runtime/model readiness plus LangGraph, LlamaIndex, Docling, Phoenix, repository, and audit status. Missing AI models return HTTP `503`; a failed configured PostgreSQL startup is reported as `degraded`.

Compose uses the named `ledgerpilot-unified-postgres-data` volume by default. This isolates the unified workflow schema and audit chain from any pre-refactor local demo volume without deleting the older data. Override `POSTGRES_VOLUME_NAME` when a different retained environment is intentional.

Open:

- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>
- Human review queue: <http://localhost:8000/review>

Run the AP happy-path demo:

```powershell
.\scripts\demo.ps1
```

Phoenix is available at <http://localhost:6006> and receives explicit spans for Ollama chat, embeddings, supervisor/domain decisions, and instrumented LangGraph activity.

OCR models are downloaded into the container's temporary cache on first image processing. For a reusable local evaluation cache, mount a volume at `/tmp` as shown in `docs/evaluation-report.md` or run the Compose API service normally.

## Local development

The evaluation dependency set is tested on Python 3.12. Use the Docker workflow when a newer host interpreter is installed.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,eval]"
uvicorn app.main:app --reload
```

Run verification:

```powershell
python -m pytest
python evaluation\run_evaluation.py
python evaluation\run_ar_evaluation.py
python evaluation\run_rag_evaluation.py
docker compose config --quiet
```

## API walkthrough

The canonical contract is [openapi/openapi.yaml](openapi/openapi.yaml). The five mandatory endpoints are implemented verbatim.

The preferred cross-domain entry point is `POST /api/v1/ingest-document`. It classifies and dispatches a document end-to-end. The mandatory `/ingest-invoice`, `/match-po`, and `/post-payment-journal` endpoints remain compatibility wrappers for the explicit AP walkthrough.

```powershell
# 1. Ingest a fixture
$ingested = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/ingest-invoice `
  -Form @{ file = Get-Item evaluation\fixtures\po-1001-invoice.json }

# 2. Run a three-way match
$body = @{ invoice_id = $ingested.invoice.id; require_goods_receipt = $true } | ConvertTo-Json
$match = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/match-po `
  -ContentType application/json -Body $body

# 3. Verify an idempotent replay. With AUTO_POST_ENABLED=true, step 2 already posted it.
$journal = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/post-payment-journal `
  -Headers @{ "Idempotency-Key" = "auto:$($ingested.invoice.id)" } `
  -ContentType application/json `
  -Body (@{ invoice_id = $ingested.invoice.id } | ConvertTo-Json)

# 4. Inspect decision provenance
Invoke-RestMethod "http://localhost:8000/api/v1/audit-log?entity_id=$($ingested.invoice.id)"
```

To test an exception, ingest `evaluation/fixtures/po-1001-price-variance.json`. Reviewers can approve or reject through `POST /api/v1/exceptions/decision`. Set `REQUIRE_HUMAN_APPROVAL=true` to require approval even for a clean match. Set `AUTO_POST_ENABLED=false` when you want matching and posting to remain two separate demo steps.

### Unified AP/AR dispatch

```powershell
# Let the parent orchestrator classify and dispatch an AR document.
$ar = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/ingest-document `
  -Form @{ file = Get-Item evaluation\fixtures\remittance-clean.json }

# Inspect the durable domain workflow state and audit provenance.
Invoke-RestMethod "http://localhost:8000/api/v1/workflows/$($ar.entity_id)"
Invoke-RestMethod "http://localhost:8000/api/v1/audit-log?entity_id=$($ar.entity_id)"
```

AR exceptions expose `RETRY_WITH_CORRECTION`, `REJECT`, and `MARK_MANUALLY_RESOLVED` at `POST /api/v1/remittance-exceptions/decision`. When global approval is enabled, `APPROVE_APPLY` is accepted only for a remittance that already passed deterministic matching. Every correction is re-matched against current ERP state before cash is applied.

### Variance-code reference

| Code | Meaning |
|---|---|
| `AMOUNT_OUT_OF_RANGE` | A monetary value exceeds the configured prototype ceiling or is negative. |
| `QUANTITY_OUT_OF_RANGE` | A quantity exceeds the prototype ceiling or is zero or negative. |
| `LINE_DETAIL_MISSING` | The invoice has no line-level detail for PO matching. |
| `LINE_AMOUNT_MISMATCH` | A line amount does not equal quantity multiplied by unit price. |
| `SUBTOTAL_MISMATCH` | The declared subtotal does not equal the sum of invoice lines. |
| `INVOICE_TOTAL_MISMATCH` | The final total does not reconcile with subtotal, tax, freight, and discount. |
| `TAX_VARIANCE` | Tax exceeds the configured percentage of invoice subtotal. |
| `FREIGHT_VARIANCE` | Freight exceeds the configured percentage of invoice subtotal. |
| `DISCOUNT_VARIANCE` | Discount exceeds the configured percentage of invoice subtotal. |
| `DUPLICATE_INVOICE` | The vendor and invoice-number combination already exists. |
| `MISSING_PO` | The referenced purchase order was not found. |
| `VENDOR_MISMATCH` | The invoice vendor differs from the purchase-order vendor. |
| `CURRENCY_MISMATCH` | The invoice currency differs from the purchase-order currency. |
| `MISSING_PO_LINE` | An invoice line cannot be mapped to a purchase-order line. |
| `PRICE_VARIANCE` | Unit price exceeds the configured tolerance. |
| `QUANTITY_VARIANCE` | Invoiced quantity exceeds the configured tolerance or ordered quantity. |
| `RECEIPT_SHORTFALL` | Invoiced quantity exceeds the goods-received quantity. |
| `TOTAL_VARIANCE` | The goods subtotal exceeds the expected PO value beyond tolerance. |

AP invoice exceptions appear at `GET /api/v1/exceptions` and support an explicit decision workflow. An approved non-PO AP exception is passed explicitly to the Mock ERP, while unapproved non-PO journal requests remain blocked. Failed AR applications appear separately at `GET /api/v1/remittance-exceptions`; `APPROVE_APPLY` approves only an already valid match awaiting configured review and can never override an invalid allocation.

## Shared mailbox configuration

Register a Microsoft Entra application with application permission `Mail.Read` scoped to the target shared mailbox, then set:

```text
GRAPH_TENANT_ID=...
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
GRAPH_MAILBOX=ap-invoices@example.com
GRAPH_FOLDER=Inbox
```

The Compose service explicitly passes these values into the API container. Call `POST /api/v1/mailbox/poll?max_messages=10`. The adapter accepts PDF, PNG/JPEG, HTML, text, and JSON attachments. Production deployment should store the client secret in a secret manager and replace polling with a Graph subscription or queue-triggered worker.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `MATCH_PRICE_TOLERANCE_PCT` | `2.0` | Maximum unit-price variance |
| `MATCH_QUANTITY_TOLERANCE_PCT` | `0.0` | Maximum ordered-quantity variance |
| `MATCH_TOTAL_TOLERANCE_PCT` | `2.0` | Maximum goods-subtotal variance against the PO |
| `MATCH_MAX_TAX_PCT` | `25.0` | Maximum tax as a percentage of invoice subtotal |
| `MATCH_MAX_FREIGHT_PCT` | `10.0` | Maximum freight as a percentage of invoice subtotal |
| `MATCH_MAX_DISCOUNT_PCT` | `30.0` | Maximum discount as a percentage of invoice subtotal |
| `MAX_MONETARY_AMOUNT` | `1000000000.00` | Positive prototype ceiling for invoice and remittance monetary fields; set per deployment and currency scope |
| `REQUIRE_HUMAN_APPROVAL` | `false` | Require explicit approval before every posting |
| `AUTO_POST_ENABLED` | `true` | Deployment policy flag for automatic posting |
| `AGENT_RUNTIME` | `ollama` | Mandatory production runtime; `fake` is rejected unless `APP_ENV=test` |
| `OLLAMA_MODEL` | `qwen3.5:2b-q4_K_M` | Local schema extraction, supervisor, and AP/AR reasoning model |
| `OLLAMA_EMBEDDING_MODEL` | `embeddinggemma` | Local 768-dimensional semantic retrieval model |
| `OLLAMA_CONTEXT_LENGTH` | `8192` | Bounded context window chosen for local hardware |
| `OLLAMA_TIMEOUT_SECONDS` | `300` | Allows the first CPU/GPU model load on constrained local hardware |
| `AGENT_MAX_STEPS` | `8` | Upper bound documented for agent workflows; current fixed graphs use at most four decisions |
| `RAG_SIMILARITY_THRESHOLD` | `0.05` | Minimum semantic similarity for retrieved policy evidence |
| `DATABASE_URL` | `memory://` locally | PostgreSQL connection string |
| `MAX_UPLOAD_MB` | `15` | Attachment size limit |

## ERP integration boundary

`app/erp.py` contains the sandbox `MockERP`. Its three operations map cleanly to a real adapter:

1. `get_purchase_order()` retrieves PO lines and received quantities.
2. `post_payment_journal()` posts an approved AP journal with an idempotency key.
3. `apply_cash()` applies an AR receipt to referenced open items.

For SAP, Oracle, or NetSuite, implement the same interface, map external IDs into the audit payload, use service-to-service authentication, and retain the upstream request/response reference rather than secrets or full sensitive documents.

## Evaluation results

The checked-in live-agent benchmark has seven synthetic AP documents and nine AR documents across JSON, PDF, scan-like PNG, text, and HTML. Non-JSON cases use the required Qwen extraction path. A separate nine-query policy set tests paraphrases, a multi-policy question, distractors, and out-of-domain abstention over the real `embeddinggemma`/LlamaIndex path.

| Metric | Result |
|---|---:|
| AP field-level extraction accuracy | 88.57% |
| Match-decision accuracy | 100.00% |
| Exception-classification accuracy | 100.00% |
| Exception-routing recall | 100.00% |
| Evaluation coverage | 100.00% |
| Straight-through-processing rate | 57.14% |
| False auto-post rate | 0.00% |
| Audit-chain integrity | 100.00% |
| AR field-level extraction accuracy | 94.44% |
| AR match-decision accuracy | 100.00% |
| False cash-application rate | 0.00% |
| RAGAS context precision | 88.89% |
| RAGAS context recall | 88.89% |

These are live local-model results, not deterministic-test-double scores. They validate controlled behavior, not production generalization. A production pilot must use representative, permissioned documents and report confidence intervals by vendor/template. See [docs/evaluation-report.md](docs/evaluation-report.md).

## Security and control posture

- Containers run as a non-root user with a read-only filesystem and `no-new-privileges`.
- API uploads are read incrementally from Starlette's spooled upload and stop at the configured size limit; mailbox attachments are allow-listed by extension.
- Deterministic duplicate detection and hash-chained audit events support duplicate and tamper controls.
- Posting requires a successful deterministic match and is idempotent.
- Human decisions require a supplied actor and comment and become audit events. The actor is not authenticated in this prototype; production requires SSO/RBAC and segregation of duties.
- No credentials are committed; `.env` is ignored.

This remains a prototype. Before production: add malware scanning, object-store encryption, row-level authorization, SSO/RBAC, secret-manager integration, queue-based workers, retention policies, PII redaction, database migrations, ERP-specific reconciliation, model/red-team evaluation, and disaster recovery. See [docs/security-and-controls.md](docs/security-and-controls.md).

## Repository map

```text
app/                    Parent orchestrator, AP/AR graphs, shared extraction, controls, adapters
db/schema.sql           PostgreSQL + pgvector schema
evaluation/             fixtures, labels, evaluator, reproducible results
tests/                  unit and workflow tests
openapi/openapi.yaml    versioned API contract
docs/                   architecture, controls, evaluation, traceability
presentation/           12-slide assignment deck
scripts/demo.ps1        end-to-end API demo
docker-compose.yml      API, Mock ERP HTTP service, PostgreSQL, Ollama, Phoenix
```
