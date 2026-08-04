"""
Customer API Routes
GET /api/v1/customers - list customers with cursor pagination
"""
import json
import asyncpg
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from uuid import UUID
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.pagination import decode_cursor
from backend.domains.customers.service import customer_service
from backend.schemas.response import APIResponse
from backend.schemas.customer import (
    CustomerNoteCreateRequest,
    CustomerNoteUpdateRequest,
    CustomerTagAttachRequest,
    CustomerTagCreateRequest,
    CustomerTagUpdateRequest,
    CustomerUpdateRequest,
)
router = APIRouter(prefix="/api/v1", tags=["Customers"])
CRM_SCHEMA_UNAVAILABLE_DETAIL = (
    "Customer CRM storage is unavailable or out of date. "
    "Run the CRM schema migrations before using customer tags, notes, or profiles."
)
async def _run_crm_db(operation, *args, **kwargs):
    try:
        return await operation(*args, **kwargs)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
        raise HTTPException(status_code=503, detail=CRM_SCHEMA_UNAVAILABLE_DETAIL) from exc
def _parse_json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
def _crm_safe_record(record: dict) -> dict:
    data = dict(record)
    for key in ("metadata", "tags", "data"):
        if key in data:
            data[key] = _parse_json_value(data[key])
    return data
def _crm_safe_records(records: list[dict]) -> list[dict]:
    return [_crm_safe_record(record) for record in records]
@router.get("/customers")
async def list_customers(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by name or phone number"),
    user: dict = Depends(get_current_user)
):
    """List customers for the current tenant with cursor pagination and optional search."""
    decoded = decode_cursor(cursor) if cursor else None
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    result = await _run_crm_db(customer_service.list_customers, tenant_id, cursor=decoded, limit=limit, search=search)
    return APIResponse.create_success(data=_crm_safe_records(result["items"]), meta={"next_cursor": result["next_cursor"]})
@router.get("/customers/{customer_id}")
async def get_customer(
    customer_id: UUID,
    user: dict = Depends(get_current_user)
):
    """Get a specific customer by ID."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    customer = await _run_crm_db(customer_service.get_customer, tenant_id, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return APIResponse.create_success(data=_crm_safe_record(customer))
@router.patch("/customers/{customer_id}")
async def update_customer(
    customer_id: UUID,
    req: CustomerUpdateRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Update a specific customer profile."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    success = await _run_crm_db(
        customer_service.update_customer,
        tenant_id, customer_id,
        name=req.name,
        email=req.email,
        consent_status=req.consent_status,
        metadata=req.metadata,
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update customer or no changes provided")
    customer = await _run_crm_db(customer_service.get_customer, tenant_id, customer_id)
    return APIResponse.create_success(data=_crm_safe_record(customer) if customer else {"updated": True, "customer_id": str(customer_id)})
from pydantic import BaseModel

class MergeCustomerRequest(BaseModel):
    secondary_customer_id: UUID

@router.post("/customers/{customer_id}/merge")
async def merge_customers(
    customer_id: UUID,
    body: MergeCustomerRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Merge a duplicate (secondary) customer into a primary customer profile."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    primary = await _run_crm_db(customer_service.get_customer, tenant_id, customer_id)
    if not primary:
        raise HTTPException(status_code=404, detail="Primary customer not found")
    secondary = await _run_crm_db(customer_service.get_customer, tenant_id, body.secondary_customer_id)
    if not secondary:
        raise HTTPException(status_code=404, detail="Secondary customer not found")

    success = await _run_crm_db(
        customer_service.merge_customers,
        tenant_id, customer_id, body.secondary_customer_id
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to merge customers")
    return APIResponse.create_success(data={
        "merged": True,
        "primary_id": str(customer_id),
        "secondary_id": str(body.secondary_customer_id)
    })
@router.get("/customers/{customer_id}/timeline")
async def get_customer_timeline(
    customer_id: UUID,
    limit: int = Query(30, ge=1, le=100),
    user: dict = Depends(get_current_user)
):
    """Get a compact CRM timeline for a customer."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    customer = await _run_crm_db(customer_service.get_customer, tenant_id, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    timeline = await _run_crm_db(customer_service.get_timeline, tenant_id, customer_id, limit=limit)
    return APIResponse.create_success(data=_crm_safe_records(timeline))
@router.get("/customer-tags")
async def list_customer_tags(user: dict = Depends(get_current_user)):
    """List CRM tags for the current tenant."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    tags = await _run_crm_db(customer_service.list_tags, tenant_id)
    return APIResponse.create_success(data=tags)
@router.post("/customer-tags")
async def create_customer_tag(
    req: CustomerTagCreateRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Create a CRM tag."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    try:
        tag = await _run_crm_db(
            customer_service.create_tag,
            tenant_id,
            name=req.name,
            color=req.color,
            description=req.description,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="A tag with this name already exists") from exc
    return APIResponse.create_success(data=tag)
@router.patch("/customer-tags/{tag_id}")
async def update_customer_tag(
    tag_id: UUID,
    req: CustomerTagUpdateRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Update a CRM tag."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    try:
        tag = await _run_crm_db(
            customer_service.update_tag,
            tenant_id,
            tag_id,
            name=req.name,
            color=req.color,
            description=req.description,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="A tag with this name already exists") from exc
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found or no changes provided")
    return APIResponse.create_success(data=tag)
@router.delete("/customer-tags/{tag_id}")
async def delete_customer_tag(
    tag_id: UUID,
    user: dict = Depends(require_tenant_admin)
):
    """Soft-delete a CRM tag."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    success = await _run_crm_db(customer_service.delete_tag, tenant_id, tag_id)
    if not success:
        raise HTTPException(status_code=404, detail="Tag not found")
    return APIResponse.create_success(data={"deleted": True, "tag_id": str(tag_id)})
@router.post("/customers/{customer_id}/tags")
async def add_customer_tag(
    customer_id: UUID,
    req: CustomerTagAttachRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Attach a tag to a customer."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    success = await _run_crm_db(customer_service.add_tag, tenant_id, customer_id, req.tag_id, created_by=user.get("user_id"))
    if not success:
        raise HTTPException(status_code=404, detail="Customer or tag not found")
    return APIResponse.create_success(data={"attached": True, "customer_id": str(customer_id), "tag_id": str(req.tag_id)})
@router.delete("/customers/{customer_id}/tags/{tag_id}")
async def remove_customer_tag(
    customer_id: UUID,
    tag_id: UUID,
    user: dict = Depends(require_tenant_admin)
):
    """Detach a tag from a customer."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    success = await _run_crm_db(customer_service.remove_tag, tenant_id, customer_id, tag_id)
    if not success:
        raise HTTPException(status_code=404, detail="Customer tag link not found")
    return APIResponse.create_success(data={"removed": True, "customer_id": str(customer_id), "tag_id": str(tag_id)})
@router.get("/customers/{customer_id}/notes")
async def list_customer_notes(
    customer_id: UUID,
    user: dict = Depends(get_current_user)
):
    """List internal CRM notes for a customer."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    customer = await _run_crm_db(customer_service.get_customer, tenant_id, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    notes = await _run_crm_db(customer_service.list_notes, tenant_id, customer_id)
    return APIResponse.create_success(data=_crm_safe_records(notes))
@router.post("/customers/{customer_id}/notes")
async def create_customer_note(
    customer_id: UUID,
    req: CustomerNoteCreateRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Create an internal CRM note for a customer."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    note = await _run_crm_db(
        customer_service.create_note,
        tenant_id,
        customer_id,
        note=req.note,
        author_user_id=user.get("user_id"),
        visibility=req.visibility,
        metadata=req.metadata,
    )
    if not note:
        raise HTTPException(status_code=404, detail="Customer not found")
    return APIResponse.create_success(data=_crm_safe_record(note))
@router.patch("/customers/{customer_id}/notes/{note_id}")
async def update_customer_note(
    customer_id: UUID,
    note_id: UUID,
    req: CustomerNoteUpdateRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Update an internal CRM note."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    note = await _run_crm_db(
        customer_service.update_note,
        tenant_id,
        customer_id,
        note_id,
        note=req.note,
        metadata=req.metadata,
    )
    if not note:
        raise HTTPException(status_code=404, detail="Note not found or no changes provided")
    return APIResponse.create_success(data=_crm_safe_record(note))
@router.delete("/customers/{customer_id}/notes/{note_id}")
async def delete_customer_note(
    customer_id: UUID,
    note_id: UUID,
    user: dict = Depends(require_tenant_admin)
):
    """Soft-delete an internal CRM note."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    success = await _run_crm_db(customer_service.delete_note, tenant_id, customer_id, note_id)
    if not success:
        raise HTTPException(status_code=404, detail="Note not found")
    return APIResponse.create_success(data={"deleted": True, "note_id": str(note_id)})
