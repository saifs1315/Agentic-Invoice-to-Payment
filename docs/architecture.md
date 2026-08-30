# Architecture and workflow

## Design principles

1. **AI interprets; controls decide.** Docling, Ollama, and LlamaIndex support extraction and context. Exact matching, tolerance enforcement, approval state, and posting eligibility remain deterministic.
2. **Every transition is attributable.** Audit events include source references, evidence, policy context, variances, actor, ERP response, timestamps, and a hash link to the previous event.
3. **Adapters isolate external systems.** Microsoft Graph and the ERP client sit behind explicit boundaries. The default Compose path uses a separate FastAPI Mock ERP over HTTP, so integration contracts, transport failures, status codes, and idempotency are testable without production credentials.
4. **Exceptions are first-class states.** The workflow stops on missing evidence or an out-of-policy variance and waits for a named human decision.
5. **Share mechanics; separate controls.** AP and AR share source registration, conversion, classification, evidence, audit, and orchestration. Their finance rules and review actions remain separate subgraphs.

## Component view

The rendered diagram is [diagrams/architecture.svg](diagrams/architecture.svg); its editable source is [diagrams/architecture.mmd](diagrams/architecture.mmd).

## Parent-orchestrator sequence

1. The Graph adapter polls a scoped shared mailbox, or a caller uploads an attachment.
2. A SHA-256 source reference is assigned before extraction.
3. The unified processor converts PDF/image/HTML/text/JSON once. Local PDF text extraction or EasyOCR handles common inputs; Docling is the rich-layout fallback. All paths retain evidence, confidence, the selected backend, and failed-backend attempt types.
4. Deterministic field and text markers classify the canonical document as `ap_invoice`, `ar_remittance`, or `unknown`. An endpoint or mailbox configuration can supply an explicit domain hint. Ambiguous input enters `classification_review` and is never posted.
5. The parent LangGraph builds the correct Pydantic-validated payload and dispatches to the AP or AR subgraph. Generic `finance_workflow_runs` persist domain, source, node, status, and state without an invoice-only foreign key.

## AP subgraph

1. LlamaIndex/pgvector retrieves AP policy context.
2. The HTTP ERP client reads the PO and Goods Receipt endpoints and builds the matching facts.
3. Deterministic code validates line arithmetic, invoice totals, duplicates, vendor/currency, tolerances, ordered quantities, and received quantities.
4. A clean match follows approval policy and posts an idempotent Payment Journal through the Mock ERP HTTP API. Exceptions enter the human queue.
5. The Mock ERP revalidates PO/vendor/currency at the posting boundary. A missing-PO journal is accepted only when the workflow transmits an explicit previously recorded human exception approval; audit events retain match evidence, policies, human decisions, and the ERP response.

## AR subgraph

1. The same canonical processor extracts a typed remittance from JSON, text, PDF, image, or HTML.
2. The AR graph retrieves cash-application policy and calls the Mock ERP open-items endpoint for the extracted customer.
3. Deterministic allocation checks duplicate reference, customer ownership (through the customer-scoped ERP read), item existence/open status, currency, and exact selected-item total.
4. Clean allocations are applied through the idempotent cash API. Partial, overpayment, currency, missing/closed item, customer, or duplicate conditions become exceptions.
5. Reviewers may correct and retry, reject, or record manual resolution. Corrections always return through the full match and current-state ERP checks. `APPROVE_APPLY` exists only for a valid match awaiting configured human approval; it cannot override an invalid allocation.

## Failure and retry behavior

- Upload validation failures return `422`; oversize files return `413`.
- Missing records return `404`; invalid workflow transitions return `409`.
- Posted, rejected, resolved, or already-approved AP invoices cannot be re-matched into an earlier state.
- ERP business conflicts return `409`; transport/server failures return `503`. Failed lookup, journal, and cash operations are audited without advancing to a posted state.
- ERP journal and cash posting use caller-supplied idempotency keys, making retries safe.
- With automatic posting enabled, `/match-po` creates the journal with `auto:{invoice_id}`; a later call to the mandatory posting endpoint should use that authoritative key to demonstrate a replay.
- Graph/ERP transport failures should be retried with bounded exponential backoff in a queue worker; the synchronous prototype returns a controlled error without silently advancing state.
- If PostgreSQL is unavailable at startup, the local prototype logs the failure, uses the in-memory repository, and reports `degraded` plus the fallback type in `/health`. Production should fail closed instead.
- `/health` uses the audit ledger's startup integrity result rather than re-hashing the unbounded event history every 30 seconds. Full chain recomputation remains available through `/api/v1/audit-log`.
