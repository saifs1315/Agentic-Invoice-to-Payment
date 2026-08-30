# Assignment traceability matrix

| Assignment requirement | Implementation evidence |
|---|---|
| Email ingestion for PDF/image/HTML | `app/email_ingestion.py`, `app/document_processing.py`, `/api/v1/mailbox/poll`; attachments enter the same parent orchestrator as API uploads |
| Contextual agentic workflow | `app/orchestrator.py` parent LangGraph plus AP `app/workflow.py` and AR `app/ar_workflow.py` subgraphs; durable generic `finance_workflow_runs`; LlamaIndex fused with pgvector/repository ranking |
| 2-way / 3-way matching | `app/matching.py`, configurable tolerances, partial quantities, bounded tax/freight/discount reconciliation, magnitude limits, and blocking arithmetic controls |
| Exception routing | variance codes, exception status, `/api/v1/exceptions`, decision endpoint, `/review` |
| Payment Journal posting via ERP API | `HttpERPClient`, Mock ERP `POST /erp/v1/payment-journals`, mandatory `/api/v1/post-payment-journal`, defensive revalidation, explicit approved non-PO exception parity, idempotency header, controlled `409`/`503` errors and failure audit |
| Mirror workflow for AR | Shared document conversion/classification; `RemittanceWorkflow` policy → deterministic match → conditional cash/review LangGraph; HTTP open-items/cash endpoints; correction/re-match decisions and unified queue |
| Sandbox or Mock API for ERP | Separate `mock-erp` FastAPI Compose service exposes PO, Goods Receipt, Payment Journal, open-item, and cash-application HTTP contracts; application defaults to `ERP_MODE=http` in Compose |
| Full audit trail | `app/audit.py`, source/evidence/policy/variance/human/ERP events, `/api/v1/audit-log` |
| PostgreSQL + pgvector | `db/schema.sql`, Compose `pgvector/pgvector:pg16` service |
| Ollama | pinned Compose service and configuration; optional local model profile |
| Arize Phoenix | Compose service and `app/observability.py` registration |
| RAGAS + extraction metrics | Nine-case paraphrase/multi-policy/abstention RAGAS evaluation; seven-document AP evaluation; nine-document AR multi-format evaluation with classification, field, match, exception, false-cash, and audit metrics |
| Mandatory five APIs | `app/api.py` and `openapi/openapi.yaml` |
| Docker Compose | `Dockerfile`, `docker-compose.yml`, `.env.example` |
| README and deployment | root `README.md` |
| Swagger/OpenAPI | FastAPI auto-docs and versioned `openapi/openapi.yaml` |
| Evaluation report | `docs/evaluation-report.md`, evaluator, dataset, fixtures |
| Architecture diagrams | editable Mermaid and rendered SVG under `docs/diagrams/` |
| 10–15 slide deck | `presentation/LedgerPilot_Assignment_Deck.pptx` |
| GitHub URL | `https://github.com/saifs1315/Agentic-Invoice-to-Payment` |
