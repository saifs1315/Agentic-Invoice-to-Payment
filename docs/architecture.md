# Architecture and workflow

## Design principles

1. **AI interprets; controls decide.** Docling, Ollama, and LlamaIndex support extraction and context. Exact matching, tolerance enforcement, approval state, and posting eligibility remain deterministic.
2. **Every transition is attributable.** Audit events include source references, evidence, policy context, variances, actor, ERP response, timestamps, and a hash link to the previous event.
3. **Adapters isolate external systems.** Microsoft Graph and ERP implementations sit behind small interfaces, allowing local fixtures and a mock ERP to demonstrate the workflow without production credentials.
4. **Exceptions are first-class states.** The workflow stops on missing evidence or an out-of-policy variance and waits for a named human decision.

## Component view

The rendered diagram is [diagrams/architecture.svg](diagrams/architecture.svg); its editable source is [diagrams/architecture.mmd](diagrams/architecture.mmd).

## AP sequence

1. The Graph adapter polls a scoped shared mailbox, or a caller uploads an attachment.
2. A SHA-256 source reference is assigned before extraction.
3. Docling converts PDF/image/HTML into normalized text; structured fields retain evidence references and confidence.
4. LangGraph invokes extraction, validation, retrieval, matching, routing, and posting nodes. Tests use the deterministic fallback when optional packages are unavailable.
5. The matcher loads PO and receipt facts from the ERP adapter and applies configured price, quantity, and total tolerances.
6. Clean invoices proceed to approval policy and idempotent posting. Exceptions enter a human queue.
7. The audit ledger records each decision and external response in a SHA-256 hash chain.

## AR sequence

The AR path shares ingestion, extraction, exception, and audit patterns. It maps a remittance reference and amount to open customer items. Exact, unambiguous allocations are applied; missing references, customer mismatches, partial payments, overpayments, or already-closed items are exceptions.

## Failure and retry behavior

- Upload validation failures return `422`; oversize files return `413`.
- Missing records return `404`; invalid workflow transitions return `409`.
- ERP posting uses a caller-supplied idempotency key, making retries safe.
- Graph/ERP transport failures should be retried with bounded exponential backoff in a queue worker; the synchronous prototype surfaces them without silently advancing state.
- If PostgreSQL is unavailable at startup, the local prototype uses the in-memory repository and reports that backend in `/health`. Production should fail closed instead.

