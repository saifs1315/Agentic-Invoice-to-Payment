# Security, controls, and production hardening

## Implemented prototype controls

- Source content receives a SHA-256 reference before processing.
- Upload size and mailbox extension allow-lists reduce malformed-input exposure.
- Duplicate vendor/invoice numbers are blocked.
- Numeric matching is deterministic and tolerance values are explicit configuration.
- Line amounts must equal quantity multiplied by unit price; subtotal and final total must reconcile with tax, freight, and discounts.
- Tax, freight, and discount amounts are bounded by configurable percentages of invoice subtotal; oversized amounts become blocking variances.
- Monetary and quantity inputs have explicit prototype limits so malformed values route to a controlled validation error instead of breaking matching. The positive monetary ceiling is deployment-configurable through `MAX_MONETARY_AMOUNT`; deployments spanning currencies must choose an appropriate limit for their currency scope.
- Three-way matching prevents invoiced quantity from exceeding goods received for the current invoice. Cumulative consumption across multiple invoices is not modeled in this prototype.
- Posting requires a successful match and, when configured, a named human approval.
- ERP posting is idempotent.
- The application container reaches ERP facts and posting only through an HTTP client boundary; the Mock ERP service independently revalidates posting facts and returns explicit conflicts.
- AR applies cash only after deterministic customer-scoped open-item, currency, and exact-amount matching. Invalid AR matches cannot be human-approved into an application; corrections must be re-matched.
- Ambiguous source documents enter classification review and never reach AP or AR posting logic.
- Audit events are append-only in the application and hash-chained.
- Secrets are supplied through environment variables; `.env` is excluded from Git.
- The API container runs as non-root, read-only, and without privilege escalation.

Prototype identity limitation: the review API records the supplied actor and comment, but does not authenticate that actor. This is useful decision provenance for a local demonstration, not an authorization control. Production requires SSO/OIDC, RBAC, approval limits, and segregation of duties before any money-moving endpoint is exposed.

## Required before production

| Area | Required control |
|---|---|
| Identity | Entra/OIDC authentication, RBAC, segregation of duties, service principals scoped to one mailbox and ERP role |
| Data | Encryption with managed keys, tenant isolation, retention/deletion policy, field-level redaction, encrypted backups |
| Documents | Malware scanning, MIME signature validation, decompression limits, sandboxed conversion, prompt-injection filtering |
| Workflow | Durable queue, bounded retries/dead-letter queue, explicit state transition guards, replay protection, dual control for manual AR resolution |
| ERP | Allow-listed journal types/accounts, amount limits, maker-checker approval, reconciliation and reversal process |
| AI | Model/version pinning, prompt registry, evaluation gates, grounded evidence, low-confidence escalation, output schema validation |
| Audit | Database permissions preventing update/delete, external WORM export, clock synchronization, SIEM alerts |
| Operations | SLOs, incident response, disaster recovery, capacity tests, vulnerability and dependency scanning |

## Threat scenarios for production testing

The list below is a threat register. Items explicitly marked as covered have automated prototype regression tests; the remainder belong in the production security test plan.

- Vendor changes bank details inside invoice text.
- PDF includes instructions intended to override the agent.
- Same invoice arrives through multiple emails or file names.
- Invoice total and line totals disagree. Covered by blocking `LINE_AMOUNT_MISMATCH`, `SUBTOTAL_MISMATCH`, and `INVOICE_TOTAL_MISMATCH` controls.
- Currency symbol conflicts with the extracted currency code.
- Invoice contains a PO belonging to another vendor.
- Journal request is replayed after a timeout. Covered by payment-journal idempotency tests.
- Human approver attempts to approve and post outside their role or limit.
