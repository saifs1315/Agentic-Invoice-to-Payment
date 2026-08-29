# Assignment traceability matrix

| Assignment requirement | Implementation evidence |
|---|---|
| Email ingestion for PDF/image/HTML | `app/email_ingestion.py`, `app/extraction.py`, `/api/v1/mailbox/poll`; Compose passes `GRAPH_*` credentials into the API |
| Contextual agentic workflow | `app/workflow.py` executable LangGraph branches; durable `workflow_runs`; `app/context.py` real LlamaIndex `VectorStoreIndex` fused with pgvector/repository ranking |
| 2-way / 3-way matching | `app/matching.py`, configurable tolerances, partial quantities, bounded tax/freight/discount reconciliation, magnitude limits, and blocking arithmetic controls |
| Exception routing | variance codes, exception status, `/api/v1/exceptions`, decision endpoint, `/review` |
| Payment Journal posting | `app/erp.py`, `/api/v1/post-payment-journal`, idempotency header |
| AR remittance matching | Structured `extract_remittance`, `/api/v1/ingest-remittance`, `MockERP.apply_cash`, durable result persistence, audit event, and read-only `/api/v1/remittance-exceptions` operator queue |
| Full audit trail | `app/audit.py`, source/evidence/policy/variance/human/ERP events, `/api/v1/audit-log` |
| PostgreSQL + pgvector | `db/schema.sql`, Compose `pgvector/pgvector:pg16` service |
| Ollama | pinned Compose service and configuration; optional local model profile |
| Arize Phoenix | Compose service and `app/observability.py` registration |
| RAGAS + extraction metrics | `evaluation/run_rag_evaluation.py` executes labeled RAGAS context precision/recall; `run_evaluation.py` covers seven documents and fails closed on document errors |
| Mandatory five APIs | `app/api.py` and `openapi/openapi.yaml` |
| Docker Compose | `Dockerfile`, `docker-compose.yml`, `.env.example` |
| README and deployment | root `README.md` |
| Swagger/OpenAPI | FastAPI auto-docs and versioned `openapi/openapi.yaml` |
| Evaluation report | `docs/evaluation-report.md`, evaluator, dataset, fixtures |
| Architecture diagrams | editable Mermaid and rendered SVG under `docs/diagrams/` |
| 10–15 slide deck | `presentation/LedgerPilot_Assignment_Deck.pptx` |
| GitHub URL | `https://github.com/saifs1315/Agentic-Invoice-to-Payment` |
