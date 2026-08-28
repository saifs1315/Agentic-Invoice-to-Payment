# LedgerPilot

LedgerPilot is an auditable prototype for agentic accounts-payable invoice processing and accounts-receivable remittance application. It ingests invoice attachments from a shared Microsoft 365 mailbox or API upload, extracts normalized finance data, performs deterministic two-way or three-way matching, routes exceptions for human review, and posts idempotent payment journals to a mock ERP adapter.

The prototype intentionally separates probabilistic AI from financial controls: Docling and optional Ollama/LlamaIndex components interpret documents and retrieve policy context; deterministic code evaluates tolerances, duplicate rules, approval requirements, and posting eligibility.

## What is included

- Microsoft Graph shared-mailbox adapter plus direct PDF, image, HTML, text, and JSON upload.
- Typed extraction with PDF text, EasyOCR, Docling fallback, optional validated Ollama JSON, and evidence references.
- LangGraph workflow that executes policy retrieval, deterministic matching, conditional posting, and review routing.
- LlamaIndex document normalization with PostgreSQL/pgvector policy retrieval and an offline fallback.
- Two-way and three-way PO/GR matching with configurable tolerances.
- Exception detection for missing PO/line, duplicate invoice, vendor/currency mismatch, price/quantity/total variance, and receipt shortfall.
- Human approval/rejection API and review queue.
- Mock ERP adapter with idempotent payment-journal posting.
- Mirrored AR remittance-to-open-item cash application.
- SHA-256 hash-chained audit events and optional PostgreSQL persistence.
- Durable PostgreSQL workflow state, journals, remittances, and policy embeddings.
- PostgreSQL 16 + pgvector schema, Ollama, and Arize Phoenix services.
- OpenAPI contract, automated tests, synthetic evaluation harness, architecture diagrams, and presentation deck.

## Architecture

![LedgerPilot architecture](docs/diagrams/architecture.svg)

The editable Mermaid sources and a detailed explanation are in [docs/architecture.md](docs/architecture.md). The implementation-to-requirement mapping is in [docs/traceability-matrix.md](docs/traceability-matrix.md).

## Quick start: Docker Compose

Prerequisites: Docker Desktop with Compose v2 and at least 6 GB of free memory for the optional AI services.

```powershell
Copy-Item .env.example .env
docker compose up --build -d postgres api
Invoke-RestMethod http://localhost:8000/api/v1/health
```

Open:

- Swagger UI: <http://localhost:8000/docs>
- ReDoc: <http://localhost:8000/redoc>
- Human review queue: <http://localhost:8000/review>

Run the AP happy-path demo:

```powershell
.\scripts\demo.ps1
```

Start optional local AI and observability services:

```powershell
docker compose --profile ai --profile observability up --build -d
docker compose exec ollama ollama pull llama3.2:3b
```

Phoenix is then available at <http://localhost:6006>.

OCR models are downloaded into the container's temporary cache on first image processing. For a reusable local evaluation cache, mount a volume at `/tmp` as shown in `docs/evaluation-report.md` or run the Compose API service normally.

## Local development

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
docker compose config --quiet
```

## API walkthrough

The canonical contract is [openapi/openapi.yaml](openapi/openapi.yaml). The five mandatory endpoints are implemented verbatim.

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

# 3. Post an idempotent journal
$journal = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/v1/post-payment-journal `
  -Headers @{ "Idempotency-Key" = "demo-$($ingested.invoice.id)" } `
  -ContentType application/json `
  -Body (@{ invoice_id = $ingested.invoice.id } | ConvertTo-Json)

# 4. Inspect decision provenance
Invoke-RestMethod "http://localhost:8000/api/v1/audit-log?entity_id=$($ingested.invoice.id)"
```

To test an exception, ingest `evaluation/fixtures/po-1001-price-variance.json`. Reviewers can approve or reject through `POST /api/v1/exceptions/decision`. Set `REQUIRE_HUMAN_APPROVAL=true` to require approval even for a clean match.

## Shared mailbox configuration

Register a Microsoft Entra application with application permission `Mail.Read` scoped to the target shared mailbox, then set:

```text
GRAPH_TENANT_ID=...
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
GRAPH_MAILBOX=ap-invoices@example.com
GRAPH_FOLDER=Inbox
```

Call `POST /api/v1/mailbox/poll?max_messages=10`. The adapter accepts PDF, PNG/JPEG, HTML, text, and JSON attachments. Production deployment should store the client secret in a secret manager and replace polling with a Graph subscription or queue-triggered worker.

## Configuration

| Variable | Default | Purpose |
|---|---:|---|
| `MATCH_PRICE_TOLERANCE_PCT` | `2.0` | Maximum unit-price variance |
| `MATCH_QUANTITY_TOLERANCE_PCT` | `0.0` | Maximum ordered-quantity variance |
| `MATCH_TOTAL_TOLERANCE_PCT` | `2.0` | Maximum invoice-total variance |
| `REQUIRE_HUMAN_APPROVAL` | `false` | Require explicit approval before every posting |
| `AUTO_POST_ENABLED` | `true` | Deployment policy flag for automatic posting |
| `OLLAMA_MODEL` | `llama3.2:3b` | Local model used by optional AI extensions |
| `LLM_EXTRACTION_ENABLED` | `false` | Ask Ollama for schema-constrained invoice JSON before regex fallback |
| `LLM_EXPLANATIONS_ENABLED` | `false` | Generate non-authoritative reviewer explanations with Ollama |
| `DATABASE_URL` | `memory://` locally | PostgreSQL connection string |
| `MAX_UPLOAD_MB` | `15` | Attachment size limit |

## ERP integration boundary

`app/erp.py` contains the sandbox `MockERP`. Its three operations map cleanly to a real adapter:

1. `get_purchase_order()` retrieves PO lines and received quantities.
2. `post_payment_journal()` posts an approved AP journal with an idempotency key.
3. `apply_cash()` applies an AR receipt to referenced open items.

For SAP, Oracle, or NetSuite, implement the same interface, map external IDs into the audit payload, use service-to-service authentication, and retain the upstream request/response reference rather than secrets or full sensitive documents.

## Evaluation results

The checked-in benchmark has six synthetic documents across JSON, PDF, and scan-like PNG: clean matches plus price-variance and missing-PO exceptions. The current reproducible results are:

| Metric | Result |
|---|---:|
| Field-level extraction accuracy | 100.00% |
| Match-decision accuracy | 100.00% |
| Exception-classification accuracy | 100.00% |
| Exception-routing recall | 100.00% |
| Straight-through-processing rate | 50.00% |
| False auto-post rate | 0.00% |
| Audit-chain integrity | 100.00% |

These numbers validate controlled behavior, not production generalization. A production pilot must use representative, permissioned invoices and report confidence intervals by vendor/template. See [docs/evaluation-report.md](docs/evaluation-report.md).

## Security and control posture

- Containers run as a non-root user with a read-only filesystem and `no-new-privileges`.
- Uploads are size-limited; mailbox attachments are allow-listed by extension.
- Deterministic duplicate detection and hash-chained audit events support duplicate and tamper controls.
- Posting requires a successful deterministic match and is idempotent.
- Human decisions require an actor and comment and become audit events.
- No credentials are committed; `.env` is ignored.

This remains a prototype. Before production: add malware scanning, object-store encryption, row-level authorization, SSO/RBAC, secret-manager integration, queue-based workers, retention policies, PII redaction, database migrations, ERP-specific reconciliation, model/red-team evaluation, and disaster recovery. See [docs/security-and-controls.md](docs/security-and-controls.md).

## Repository map

```text
app/                    API, domain, workflow, extraction, matching, adapters
db/schema.sql           PostgreSQL + pgvector schema
evaluation/             fixtures, labels, evaluator, reproducible results
tests/                  unit and workflow tests
openapi/openapi.yaml    versioned API contract
docs/                   architecture, controls, evaluation, traceability
presentation/           12-slide assignment deck
scripts/demo.ps1        end-to-end API demo
docker-compose.yml      API, PostgreSQL, Ollama, Phoenix
```
