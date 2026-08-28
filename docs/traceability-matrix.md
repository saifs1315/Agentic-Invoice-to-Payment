# Assignment traceability matrix

| Assignment requirement | Implementation evidence |
|---|---|
| Email ingestion for PDF/image/HTML | `app/email_ingestion.py`, `app/extraction.py`, `/api/v1/mailbox/poll` |
| Contextual agentic workflow | `app/workflow.py` LangGraph state graph; `app/context.py` LlamaIndex retrieval |
| 2-way / 3-way matching | `app/matching.py`, configurable tolerances in `app/config.py` |
| Exception routing | variance codes, exception status, `/api/v1/exceptions`, decision endpoint, `/review` |
| Payment Journal posting | `app/erp.py`, `/api/v1/post-payment-journal`, idempotency header |
| AR remittance matching | `extract_remittance`, `/api/v1/ingest-remittance`, `MockERP.apply_cash` |
| Full audit trail | `app/audit.py`, source/evidence/policy/variance/human/ERP events, `/api/v1/audit-log` |
| PostgreSQL + pgvector | `db/schema.sql`, Compose `pgvector/pgvector:pg16` service |
| Ollama | pinned Compose service and configuration; optional local model profile |
| Arize Phoenix | Compose service and `app/observability.py` registration |
| RAGAS + extraction metrics | optional eval dependency; metric rationale and plan in evaluation report |
| Mandatory five APIs | `app/api.py` and `openapi/openapi.yaml` |
| Docker Compose | `Dockerfile`, `docker-compose.yml`, `.env.example` |
| README and deployment | root `README.md` |
| Swagger/OpenAPI | FastAPI auto-docs and versioned `openapi/openapi.yaml` |
| Evaluation report | `docs/evaluation-report.md`, evaluator, dataset, fixtures |
| Architecture diagrams | editable Mermaid and rendered SVG under `docs/diagrams/` |
| 10–15 slide deck | `presentation/LedgerPilot_Assignment_Deck.pptx` |
| GitHub URL | Requires user authorization/authentication for external publication |

