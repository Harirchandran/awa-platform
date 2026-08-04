from typing import Optional
from uuid import UUID
from io import BytesIO
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from backend.ai.rag_service import rag_service
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.entitlements import require_module_config_write
from backend.domains.knowledge.service import knowledge_service
from backend.knowledge.ingestion import KnowledgeIngestionError, knowledge_ingestion_service
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1/knowledge-base", tags=["Knowledge Base"])
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
SUPPORTED_UPLOAD_TYPES = {
    "text/plain",
    "text/markdown",
    "application/pdf",
}
class KnowledgeUpdateRequest(BaseModel):
    document_title: Optional[str] = Field(default=None, min_length=2, max_length=160)
    chunk_text: Optional[str] = Field(default=None, min_length=10, max_length=4000)
    enabled: Optional[bool] = None
    @field_validator("document_title", "chunk_text")
    @classmethod
    def strip_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value
class KnowledgeCreateRequest(BaseModel):
    document_title: str = Field(min_length=2, max_length=160)
    chunk_text: str = Field(min_length=10, max_length=24000)
    source: str = Field(default="manual", pattern="^(manual|document)$")
    file_name: Optional[str] = Field(default=None, max_length=240)
    @field_validator("document_title", "chunk_text", "file_name")
    @classmethod
    def strip_create_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value
class WebsiteImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    title: Optional[str] = Field(default=None, max_length=160)
    @field_validator("url", "title")
    @classmethod
    def strip_website_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty")
        return value
def _tenant_id_from_user(user: dict) -> UUID:
    return UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
def _json_safe_record(record: dict) -> dict:
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else str(value) if isinstance(value, UUID) else value
        for key, value in dict(record).items()
    }
async def _extract_upload_text(file: UploadFile) -> str:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Document is too large")
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    filename = (file.filename or "document").lower()
    if content_type not in SUPPORTED_UPLOAD_TYPES and not filename.endswith((".txt", ".md", ".pdf")):
        raise HTTPException(status_code=415, detail="Only TXT, Markdown, and PDF documents are supported")
    if content_type == "application/pdf" or filename.endswith(".pdf"):
        try:
            import asyncio
            import pypdf
            pypdf.filters.ZLIB_MAX_OUTPUT_LENGTH = 10_000_000
            pypdf.filters.LZW_MAX_OUTPUT_LENGTH = 10_000_000
            pypdf.filters.RUN_LENGTH_MAX_OUTPUT_LENGTH = 10_000_000
            pypdf.filters.MAX_DECLARED_STREAM_LENGTH = 10_000_000
            from pypdf import PdfReader
            def _extract():
                reader = PdfReader(BytesIO(content))
                if len(reader.pages) > 50:
                    raise ValueError("PDF exceeds maximum allowed pages (50)")
                return "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
            try:
                return await asyncio.wait_for(asyncio.to_thread(_extract), timeout=10.0)
            except asyncio.TimeoutError as te:
                raise HTTPException(status_code=408, detail="PDF text extraction timed out") from te
        except HTTPException:
            raise
        except Exception as exc:
            if isinstance(exc, ValueError) and "PDF exceeds maximum" in str(exc):
                raise HTTPException(status_code=400, detail=str(exc))
            raise HTTPException(status_code=422, detail="Could not extract readable PDF text") from exc
    return content.decode("utf-8", errors="ignore").strip()
@router.get("")
async def list_knowledge_base_entries(
    source: Optional[str] = Query(default=None, max_length=80),
    search: Optional[str] = Query(default=None, max_length=120),
    limit: int = Query(default=100, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """List tenant knowledge entries, including escalation-sourced answers."""
    tenant_id = _tenant_id_from_user(user)
    rows = await knowledge_service.list_entries(
        tenant_id=tenant_id,
        source=source,
        search=search,
        limit=limit,
    )
    return APIResponse.create_success(data=[_json_safe_record(row) for row in rows])
@router.get("/sources")
async def summarize_knowledge_sources(
    user: dict = Depends(get_current_user),
):
    """Summarize tenant knowledge source status for the dashboard."""
    tenant_id = _tenant_id_from_user(user)
    rows = await knowledge_service.summarize_sources(tenant_id)
    return APIResponse.create_success(data=[_json_safe_record(row) for row in rows])
@router.post("")
async def create_knowledge_base_entry(
    body: KnowledgeCreateRequest,
    user: dict = Depends(require_tenant_admin),
):
    """Create tenant-managed manual or document knowledge entries."""
    tenant_id = _tenant_id_from_user(user)
    await require_module_config_write(tenant_id, "faq")
    try:
        if body.source == "document":
            rows = await knowledge_ingestion_service.create_document_entry(
                tenant_id,
                body.document_title,
                body.chunk_text,
                body.file_name,
            )
        else:
            rows = await knowledge_ingestion_service.create_manual_entry(
                tenant_id,
                body.document_title,
                body.chunk_text,
            )
    except KnowledgeIngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return APIResponse.create_success(data=[_json_safe_record(row) for row in rows])
@router.post("/import-website")
async def import_website_knowledge(
    body: WebsiteImportRequest,
    user: dict = Depends(require_tenant_admin),
):
    """Import readable public website text into tenant knowledge."""
    tenant_id = _tenant_id_from_user(user)
    await require_module_config_write(tenant_id, "faq")
    try:
        rows = await knowledge_ingestion_service.import_website(tenant_id, body.url, body.title)
    except KnowledgeIngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read this website right now") from exc
    return APIResponse.create_success(data=[_json_safe_record(row) for row in rows])
@router.post("/upload-document")
async def upload_document_knowledge(
    title: str = Form(..., min_length=2, max_length=160),
    file: UploadFile = File(...),
    user: dict = Depends(require_tenant_admin),
):
    """Upload a supported text/PDF document into tenant knowledge."""
    tenant_id = _tenant_id_from_user(user)
    await require_module_config_write(tenant_id, "faq")
    text = await _extract_upload_text(file)
    try:
        rows = await knowledge_ingestion_service.create_document_entry(
            tenant_id,
            title,
            text,
            file.filename,
        )
    except KnowledgeIngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return APIResponse.create_success(data=[_json_safe_record(row) for row in rows])
@router.patch("/{entry_id}")
async def update_knowledge_base_entry(
    entry_id: UUID,
    body: KnowledgeUpdateRequest,
    user: dict = Depends(require_tenant_admin),
):
    """Edit or enable/disable one tenant-owned knowledge entry."""
    await require_module_config_write(_tenant_id_from_user(user), "faq")
    if body.document_title is None and body.chunk_text is None and body.enabled is None:
        raise HTTPException(status_code=400, detail="Provide a title, answer text, or enabled state")
    tenant_id = _tenant_id_from_user(user)
    embedding = await rag_service.embed_query(body.chunk_text) if body.chunk_text is not None else None
    metadata_patch = {}
    if body.enabled is not None:
        metadata_patch["enabled"] = body.enabled
    row = await knowledge_service.update_entry(
        tenant_id=tenant_id,
        entry_id=entry_id,
        document_title=body.document_title,
        chunk_text=body.chunk_text,
        embedding=embedding,
        metadata_patch=metadata_patch,
        replace_embedding=body.chunk_text is not None,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return APIResponse.create_success(data=_json_safe_record(row))
@router.delete("/{entry_id}")
async def delete_knowledge_base_entry(
    entry_id: UUID,
    user: dict = Depends(require_tenant_admin),
):
    """Delete one tenant-owned knowledge entry."""
    tenant_id = _tenant_id_from_user(user)
    await require_module_config_write(tenant_id, "faq")
    deleted = await knowledge_service.delete_entry(tenant_id, entry_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Knowledge entry not found")
    return APIResponse.create_success(data={"deleted": True})
