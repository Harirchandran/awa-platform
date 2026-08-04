-- =================================================================
-- Migration 007: Enterprise RAG (pgvector)
-- Supports advanced knowledge base document search for AI Concierge.
-- =================================================================
BEGIN;
-- Enable the pgvector extension (Supabase supports this natively)
CREATE EXTENSION IF NOT EXISTS vector;
-- Table to hold vectorized documents or FAQs per tenant
CREATE TABLE IF NOT EXISTS tenant_knowledge_base (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    document_title TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1536), -- 1536 dimensions for OpenAI text-embedding-3-small
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- Note: In production, HNSW or IVFFlat indexes should be created here on the embedding column
-- CREATE INDEX ON tenant_knowledge_base USING hnsw (embedding vector_cosine_ops);
COMMIT;