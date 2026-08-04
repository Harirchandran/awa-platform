from uuid import uuid4
import pytest
from backend.knowledge.ingestion import (
    KnowledgeIngestionError,
    chunk_text,
    knowledge_ingestion_service,
    validate_public_url,
)
pytestmark = pytest.mark.anyio
def test_chunk_text_splits_large_sources():
    chunks = chunk_text(("Policy text. " * 500) + "\n\nSecond section.")
    assert len(chunks) >= 2
    assert all(len(chunk) <= 3500 for chunk in chunks)
def test_validate_public_url_blocks_private_targets():
    with pytest.raises(KnowledgeIngestionError):
        validate_public_url("http://127.0.0.1:8000/private")
async def test_create_manual_entry_stores_lexical_metadata_when_embedding_missing(monkeypatch):
    created = []
    async def fake_embed(text):
        return []
    async def fake_create(**kwargs):
        created.append(kwargs)
        return {"id": uuid4(), **kwargs}
    monkeypatch.setattr("backend.knowledge.ingestion.rag_service.embed_query", fake_embed)
    monkeypatch.setattr("backend.knowledge.ingestion.knowledge_service.create_entry", fake_create)
    rows = await knowledge_ingestion_service.create_manual_entry(
        uuid4(),
        "Parking",
        "Customers can park behind the clinic near the blue gate.",
    )
    assert len(rows) == 1
    assert created[0]["metadata"]["source"] == "manual"
    assert created[0]["metadata"]["enabled"] is True
    assert created[0]["metadata"]["processing_mode"] == "lexical_search"
async def test_sync_faqs_replaces_existing_faq_source(monkeypatch):
    calls = {"deleted": False, "created": 0}
    async def fake_delete(tenant_id, source):
        calls["deleted"] = source == "faq"
        return 2
    async def fake_embed(text):
        return []
    async def fake_create(**kwargs):
        calls["created"] += 1
        return {"id": uuid4(), **kwargs}
    monkeypatch.setattr("backend.knowledge.ingestion.knowledge_service.delete_entries_by_source", fake_delete)
    monkeypatch.setattr("backend.knowledge.ingestion.rag_service.embed_query", fake_embed)
    monkeypatch.setattr("backend.knowledge.ingestion.knowledge_service.create_entry", fake_create)
    count = await knowledge_ingestion_service.sync_faqs(
        uuid4(),
        [{"id": "faq-1", "question": "Hours?", "answer": "Open 9 to 6."}],
    )
    assert count == 1
    assert calls == {"deleted": True, "created": 1}

async def test_import_website_deduplicates_chunks(monkeypatch):
    calls = {"deleted_url": None, "created_count": 0}
    tenant_id = uuid4()
    target_url = "https://example.com/about"

    async def fake_delete_url(t_id, url):
        assert t_id == tenant_id
        calls["deleted_url"] = url
        return 3

    async def fake_embed(text):
        return [0.1] * 1536

    async def fake_create(**kwargs):
        calls["created_count"] += 1
        return {"id": uuid4(), **kwargs}

    def fake_fetch(url, resolved_ip=None):
        return "<html><body><h1>About Us</h1><p>We are a healthcare clinic. " * 20 + "</body></html>"

    monkeypatch.setattr("backend.knowledge.ingestion.knowledge_service.delete_entries_by_url", fake_delete_url)
    monkeypatch.setattr("backend.knowledge.ingestion.rag_service.embed_query", fake_embed)
    monkeypatch.setattr("backend.knowledge.ingestion.knowledge_service.create_entry", fake_create)
    monkeypatch.setattr("backend.knowledge.ingestion._fetch_url_text_sync", fake_fetch)

    rows = await knowledge_ingestion_service.import_website(tenant_id, target_url, "About Us")

    # Verify that delete_entries_by_url was called with target_url
    assert calls["deleted_url"] == target_url
    # Verify new chunks were created
    assert calls["created_count"] > 0
    assert len(rows) == calls["created_count"]

def test_validate_public_url_returns_ip_and_blocks_rebinding():
    # Valid public URL
    clean, ip = validate_public_url("https://www.google.com/")
    assert clean == "https://www.google.com/"
    assert ip is not None

    # Blocks private IP
    with pytest.raises(KnowledgeIngestionError):
        validate_public_url("http://192.168.1.1/info")
