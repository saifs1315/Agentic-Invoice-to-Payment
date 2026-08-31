# Architecture and workflow

## Design principles

1. **AI operates; controls constrain.** The local Qwen agent classifies, extracts unstructured documents, selects tools, and chooses bounded next actions. Exact matching, tolerance enforcement, approval state, action allow-lists, and posting eligibility remain deterministic.
2. **Every transition is attributable.** Audit events include source references, evidence, policy context, variances, actor, ERP response, timestamps, and a hash link to the previous event.
3. **Adapters isolate external systems.** Microsoft Graph and the ERP client sit behind explicit boundaries. The default Compose path uses a separate FastAPI Mock ERP over HTTP, so integration contracts, transport failures, status codes, and idempotency are testable without production credentials.
4. **Exceptions are first-class states.** The workflow stops on missing evidence or an out-of-policy variance and waits for a named human decision.
5. **Share mechanics; separate controls.** AP and AR share source registration, conversion, classification, evidence, audit, and orchestration. Their finance rules and review actions remain separate subgraphs.

## Component view

The rendered diagram is [diagrams/architecture.svg](diagrams/architecture.svg); its editable source is [diagrams/architecture.mmd](diagrams/architecture.mmd).

## Parent-orchestrator sequence

1. The Graph adapter polls a scoped shared mailbox, or a caller uploads an attachment.
2. A SHA-256 source reference is assigned before extraction.
3. The orchestrator invokes the unified document processor as a tool. PDF text extraction or EasyOCR handles common inputs; Docling is the rich-layout fallback. The tool returns canonical text, deterministic candidate classification, evidence, confidence, and backend attempts, but it cannot dispatch or post.
4. The mandatory supervisor agent observes that evidence and returns schema-constrained `DISPATCH_AP`, `DISPATCH_AR`, or `ESCALATE_CLASSIFICATION`. A conflict with strong deterministic evidence is forced to review; ambiguity is never posted.
5. The parent LangGraph builds a Pydantic-validated payload. Non-JSON documents use schema-constrained Qwen extraction; structured JSON remains deterministic input parsing, while supervisor and domain agent decisions are still mandatory. The graph then invokes the AP or AR agent subgraph.
6. Generic `finance_workflow_runs` persist domain, source, current node, decisions, retrieved context, and results. The audit chain separately records each processor tool completion and agent decision.

## AP subgraph

1. The AP agent chooses the allow-listed `RETRIEVE_POLICY` tool. `embeddinggemma`, LlamaIndex, and pgvector return labeled policy evidence.
2. The agent chooses `RUN_AP_MATCH`; the HTTP ERP tool reads the PO and Goods Receipt and deterministic code validates arithmetic, duplicates, vendor/currency, tolerances, quantities, and receipts.
3. The agent observes the control result and must choose the single permitted guarded outcome: `POST_PAYMENT_JOURNAL` for an eligible clean match or `ESCALATE` otherwise.
4. The posting tool independently revalidates successful match/approval state and idempotency before calling the Mock ERP. Exceptions enter the human queue.
5. The Mock ERP revalidates PO/vendor/currency at the posting boundary. A missing-PO journal is accepted only when the workflow transmits an explicit previously recorded human exception approval; audit events retain match evidence, policies, human decisions, and the ERP response.

## AR subgraph

1. The same processor tool and supervisor produce and route a typed remittance from JSON, text, PDF, image, or HTML.
2. The AR agent chooses `RETRIEVE_POLICY`, then `RUN_AR_MATCH`; the ERP tool supplies current open items.
3. Deterministic allocation checks duplicate reference, customer ownership, item existence/open status, currency, and exact selected-item total.
4. The agent must choose `APPLY_CASH` only when those controls pass, otherwise `ESCALATE`. The cash tool revalidates state and idempotency at the ERP boundary.
5. Reviewers may correct and retry, reject, or record manual resolution. Corrections always return through the full match and current-state ERP checks. `APPROVE_APPLY` exists only for a valid match awaiting configured human approval; it cannot override an invalid allocation.

## Failure and retry behavior

- Upload validation failures return `422`; oversize files return `413`.
- Missing Ollama, Qwen, or embedding model readiness returns `503`; there is no production non-AI completion path. The deterministic fake runtime is accepted only with `APP_ENV=test`.
- Missing records return `404`; invalid workflow transitions return `409`.
- Posted, rejected, resolved, or already-approved AP invoices cannot be re-matched into an earlier state.
- ERP business conflicts return `409`; transport/server failures return `503`. Failed lookup, journal, and cash operations are audited without advancing to a posted state.
- ERP journal and cash posting use caller-supplied idempotency keys, making retries safe.
- With automatic posting enabled, `/match-po` creates the journal with `auto:{invoice_id}`; a later call to the mandatory posting endpoint should use that authoritative key to demonstrate a replay.
- Graph/ERP transport failures should be retried with bounded exponential backoff in a queue worker; the synchronous prototype returns a controlled error without silently advancing state.
- If PostgreSQL is unavailable at startup, the local prototype logs the failure, uses the in-memory repository, and reports `degraded` plus the fallback type in `/health`. Production should fail closed instead.
- `/health` uses the audit ledger's startup integrity result rather than re-hashing the unbounded event history every 30 seconds. Full chain recomputation remains available through `/api/v1/audit-log`.
