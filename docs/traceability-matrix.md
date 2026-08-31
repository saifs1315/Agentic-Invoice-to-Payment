# Assignment traceability matrix

| Assignment requirement | Implementation evidence |
|---|---|
| Email ingestion for PDF/image/HTML | `app/email_ingestion.py`, `app/document_processing.py`, `/api/v1/mailbox/poll`; attachments enter the same parent orchestrator as API uploads |
| Docling document processing | `UnifiedDocumentProcessor` uses Docling by default for HTML/rich layouts and as the explicit/fallback processor for other formats; PDFium/EasyOCR low-memory paths and every fallback are recorded in extraction provenance |
| Contextual agentic workflow | Mandatory `OllamaAgentRuntime`; schema-constrained supervisor in `app/orchestrator.py`; bounded AP `app/workflow.py` and AR `app/ar_workflow.py` observe-reason-act subgraphs; allow-listed tool actions; agent-query validation with governed fallback/fail-closed retrieval; durable decision state and audit events |
| LlamaIndex RAG framework | `ContextRetriever` builds a real `VectorStoreIndex` with the Ollama EmbeddingGemma adapter; live RAG evaluation fails unless that semantic index is active |
| 2-way / 3-way matching | `app/matching.py`, configurable tolerances, partial quantities, bounded tax/freight/discount reconciliation, magnitude limits, and blocking arithmetic controls |
| Exception routing | variance codes, exception status, `/api/v1/exceptions`, decision endpoint, `/review` |
| Payment Journal posting via ERP API | `HttpERPClient`, Mock ERP `POST /erp/v1/payment-journals`, mandatory `/api/v1/post-payment-journal`, `MATCHED`/`APPROVED` status allow-list, escalation/rejection bypass regression tests, defensive ERP revalidation, explicit approved non-PO exception parity, idempotency header, controlled `409`/`503` errors and failure audit |
| Mirror workflow for AR | Same processor-tool → supervisor → semantic policy retrieval → ERP observation → deterministic control → bounded agent action pattern as AP; `RemittanceWorkflow` uses HTTP open-items/cash tools, correction/re-match, and unified review |
| Sandbox or Mock API for ERP | Separate `mock-erp` FastAPI Compose service exposes PO, Goods Receipt, Payment Journal, open-item, and cash-application HTTP contracts; application defaults to `ERP_MODE=http` in Compose |
| Full audit trail | `app/audit.py`, source/evidence/policy/variance/human/ERP events, `/api/v1/audit-log` |
| PostgreSQL + pgvector | `db/schema.sql`, Compose `pgvector/pgvector:pg16` service |
| Ollama | Mandatory pinned `ollama/ollama:0.30.8`, model-init gate, `qwen3.5:2b-q4_K_M`, `embeddinggemma`, fail-closed health, test-only fake runtime guard |
| Arize Phoenix | Mandatory Compose service, `app/observability.py` registration, explicit Ollama chat/embedding spans, LangGraph auto-instrumentation, `/health` capability evidence |
| RAGAS + extraction metrics | Nine-case paraphrase/multi-policy/abstention RAGAS evaluation; seven-document AP evaluation; nine-document AR multi-format evaluation with classification, field, match, exception, conservative-escalation, false-action, and audit metrics |
| Mandatory five APIs | `app/api.py` and `openapi/openapi.yaml` |
| Docker Compose | `Dockerfile`, `docker-compose.yml`, `.env.example` |
| README and deployment | root `README.md` |
| Swagger/OpenAPI | FastAPI auto-docs and versioned `openapi/openapi.yaml` |
| Evaluation report | `docs/evaluation-report.md`, evaluator, dataset, fixtures |
| Architecture diagrams | editable Mermaid and rendered SVG under `docs/diagrams/` |
| 10–15 slide deck | `presentation/LedgerPilot_Assignment_Deck.pptx` |
| GitHub URL | `https://github.com/saifs1315/Agentic-Invoice-to-Payment` |
