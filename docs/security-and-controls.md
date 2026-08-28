# Security, controls, and production hardening

## Implemented prototype controls

- Source content receives a SHA-256 reference before processing.
- Upload size and mailbox extension allow-lists reduce malformed-input exposure.
- Duplicate vendor/invoice numbers are blocked.
- Numeric matching is deterministic and tolerance values are explicit configuration.
- Three-way matching prevents invoiced quantity from exceeding goods received.
- Posting requires a successful match and, when configured, a named human approval.
- ERP posting is idempotent.
- Audit events are append-only in the application and hash-chained.
- Secrets are supplied through environment variables; `.env` is excluded from Git.
- The API container runs as non-root, read-only, and without privilege escalation.

## Required before production

| Area | Required control |
|---|---|
| Identity | Entra/OIDC authentication, RBAC, segregation of duties, service principals scoped to one mailbox and ERP role |
| Data | Encryption with managed keys, tenant isolation, retention/deletion policy, field-level redaction, encrypted backups |
| Documents | Malware scanning, MIME signature validation, decompression limits, sandboxed conversion, prompt-injection filtering |
| Workflow | Durable queue, retries/dead-letter queue, explicit state transition guards, replay protection |
| ERP | Allow-listed journal types/accounts, amount limits, maker-checker approval, reconciliation and reversal process |
| AI | Model/version pinning, prompt registry, evaluation gates, grounded evidence, low-confidence escalation, output schema validation |
| Audit | Database permissions preventing update/delete, external WORM export, clock synchronization, SIEM alerts |
| Operations | SLOs, incident response, disaster recovery, capacity tests, vulnerability and dependency scanning |

## Threat-focused tests

- Vendor changes bank details inside invoice text.
- PDF includes instructions intended to override the agent.
- Same invoice arrives through multiple emails or file names.
- Invoice total and line totals disagree.
- Currency symbol conflicts with the extracted currency code.
- Invoice contains a PO belonging to another vendor.
- Journal request is replayed after a timeout.
- Human approver attempts to approve and post outside their role or limit.

