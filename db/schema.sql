CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS source_documents (
    id UUID PRIMARY KEY,
    source_ref TEXT UNIQUE NOT NULL,
    media_type TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policy_documents (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoices (
    id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL,
    invoice_number TEXT NOT NULL,
    invoice_date DATE NOT NULL,
    currency CHAR(3) NOT NULL,
    total NUMERIC(18,2) NOT NULL,
    po_number TEXT,
    status TEXT NOT NULL,
    confidence NUMERIC(5,4) NOT NULL,
    source_ref TEXT NOT NULL,
    extracted_data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE invoices DROP CONSTRAINT IF EXISTS invoices_vendor_id_invoice_number_key;

CREATE TABLE IF NOT EXISTS match_results (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(id),
    match_type TEXT NOT NULL,
    matched BOOLEAN NOT NULL,
    variances JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    invoice_id TEXT PRIMARY KEY REFERENCES invoices(id),
    current_node TEXT NOT NULL,
    state JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS finance_workflow_runs (
    entity_id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL CHECK (workflow_type IN ('ap', 'ar', 'classification')),
    source_ref TEXT,
    current_node TEXT NOT NULL,
    status TEXT NOT NULL,
    state JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payment_journals (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(id),
    idempotency_key TEXT UNIQUE NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS remittances (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    reference TEXT NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    currency CHAR(3) NOT NULL,
    status TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence BIGSERIAL PRIMARY KEY,
    id TEXT UNIQUE NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload JSONB NOT NULL,
    previous_hash CHAR(64),
    event_hash CHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS invoices_status_idx ON invoices(status);
CREATE INDEX IF NOT EXISTS invoices_vendor_number_idx ON invoices(vendor_id, invoice_number);
CREATE INDEX IF NOT EXISTS audit_entity_idx ON audit_events(entity_id, sequence);
CREATE INDEX IF NOT EXISTS source_embedding_hnsw_idx ON source_documents USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS policy_embedding_hnsw_idx ON policy_documents USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS finance_workflow_status_idx ON finance_workflow_runs(workflow_type, status);
