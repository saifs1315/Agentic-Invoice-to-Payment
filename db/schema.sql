CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS source_documents (
    id UUID PRIMARY KEY,
    source_ref TEXT UNIQUE NOT NULL,
    media_type TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    embedding vector(768),
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vendor_id, invoice_number)
);

CREATE TABLE IF NOT EXISTS match_results (
    id TEXT PRIMARY KEY,
    invoice_id TEXT NOT NULL REFERENCES invoices(id),
    match_type TEXT NOT NULL,
    matched BOOLEAN NOT NULL,
    variances JSONB NOT NULL,
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
CREATE INDEX IF NOT EXISTS audit_entity_idx ON audit_events(entity_id, sequence);
CREATE INDEX IF NOT EXISTS source_embedding_hnsw_idx ON source_documents USING hnsw (embedding vector_cosine_ops);

