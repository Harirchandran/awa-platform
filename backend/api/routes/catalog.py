"""Catalog and inventory API routes."""
import json
from decimal import Decimal
from typing import Optional
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.api.entitlements import require_module_config_write
from backend.domains.catalog.service import catalog_service
from backend.domains.media.repository import media_repository
from backend.domains.media.service import sanitize_media_record
from backend.schemas.catalog import (
    CatalogCategoryCreateRequest,
    CatalogCategoryUpdateRequest,
    CatalogItemCreateRequest,
    CatalogItemUpdateRequest,
    InventoryAdjustRequest,
)
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["Catalog"])
CATALOG_SCHEMA_UNAVAILABLE_DETAIL = (
    "Catalog storage is unavailable or out of date. "
    "Run the catalog schema migrations before using catalog and inventory features."
)
async def _run_catalog_db(operation, *args, **kwargs):
    try:
        return await operation(*args, **kwargs)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
        raise HTTPException(status_code=503, detail=CATALOG_SCHEMA_UNAVAILABLE_DETAIL) from exc
def _tenant_id(user: dict) -> UUID:
    return UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
def _user_id(user: dict) -> Optional[UUID]:
    value = user.get("user_id")
    return UUID(value) if isinstance(value, str) else value
def _parse_json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
def _number_value(value):
    if isinstance(value, Decimal):
        return float(value)
    return value
def _catalog_safe_record(record: dict) -> dict:
    data = dict(record)
    for key in ("metadata", "event_data"):
        if key in data:
            data[key] = _parse_json_value(data[key])
    for key in ("price", "stock_quantity", "low_stock_threshold", "quantity_delta"):
        if key in data:
            data[key] = _number_value(data[key])
    return data
def _catalog_safe_records(records: list[dict]) -> list[dict]:
    return [_catalog_safe_record(record) for record in records]

async def _with_catalog_item_images(tenant_id: UUID, records: list[dict]) -> list[dict]:
    safe_records = _catalog_safe_records(records)
    if not safe_records:
        return safe_records
    try:
        images = await media_repository.list_primary_assets(
            tenant_id,
            "catalog_item",
            [record["id"] for record in records if record.get("id")],
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        images = {}
    for record in safe_records:
        record["image"] = sanitize_media_record(images.get(str(record.get("id"))))
    return safe_records

async def _with_catalog_item_image(tenant_id: UUID, record: dict) -> dict:
    safe_record = _catalog_safe_record(record)
    try:
        image = await media_repository.get_primary_asset(tenant_id, "catalog_item", record["id"])
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        image = None
    safe_record["image"] = sanitize_media_record(image)
    return safe_record

@router.get("/catalog/categories")
async def list_catalog_categories(user: dict = Depends(get_current_user)):
    categories = await _run_catalog_db(catalog_service.list_categories, _tenant_id(user))
    return APIResponse.create_success(data=_catalog_safe_records(categories))
@router.post("/catalog/categories")
async def create_catalog_category(
    req: CatalogCategoryCreateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "order")
    try:
        category = await _run_catalog_db(
            catalog_service.create_category,
            _tenant_id(user),
            name=req.name,
            description=req.description,
            sort_order=req.sort_order,
            is_active=req.is_active,
            metadata=req.metadata,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="A category with this name already exists") from exc
    return APIResponse.create_success(data=_catalog_safe_record(category))
@router.patch("/catalog/categories/{category_id}")
async def update_catalog_category(
    category_id: UUID,
    req: CatalogCategoryUpdateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "order")
    try:
        category = await _run_catalog_db(
            catalog_service.update_category,
            _tenant_id(user),
            category_id,
            req.model_dump(exclude_unset=True),
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="A category with this name already exists") from exc
    if not category:
        raise HTTPException(status_code=404, detail="Category not found or no changes provided")
    return APIResponse.create_success(data=_catalog_safe_record(category))
@router.delete("/catalog/categories/{category_id}")
async def delete_catalog_category(
    category_id: UUID,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "order")
    success = await _run_catalog_db(catalog_service.delete_category, _tenant_id(user), category_id)
    if not success:
        raise HTTPException(status_code=404, detail="Category not found")
    return APIResponse.create_success(data={"deleted": True, "category_id": str(category_id)})
@router.get("/catalog/items")
async def list_catalog_items(
    category_id: Optional[UUID] = Query(None),
    search: Optional[str] = Query(None, max_length=120),
    active_only: bool = Query(False),
    available_only: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    items = await _run_catalog_db(
        catalog_service.list_items,
        _tenant_id(user),
        category_id=category_id,
        search=search,
        active_only=active_only,
        available_only=available_only,
    )
    return APIResponse.create_success(data=await _with_catalog_item_images(_tenant_id(user), items))
@router.post("/catalog/items")
async def create_catalog_item(
    req: CatalogItemCreateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "order")
    try:
        item = await _run_catalog_db(
            catalog_service.create_item,
            _tenant_id(user),
            name=req.name,
            category_id=req.category_id,
            description=req.description,
            price=req.price,
            currency=req.currency,
            sku=req.sku,
            image_url=req.image_url,
            is_active=req.is_active,
            is_available=req.is_available,
            stock_tracking_enabled=req.stock_tracking_enabled,
            stock_quantity=req.stock_quantity,
            low_stock_threshold=req.low_stock_threshold,
            metadata=req.metadata,
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="An active item with this SKU already exists") from exc
    if not item:
        raise HTTPException(status_code=404, detail="Category not found")
    return APIResponse.create_success(data=await _with_catalog_item_image(_tenant_id(user), item))
@router.get("/catalog/items/{item_id}")
async def get_catalog_item(item_id: UUID, user: dict = Depends(get_current_user)):
    item = await _run_catalog_db(catalog_service.get_item, _tenant_id(user), item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    return APIResponse.create_success(data=await _with_catalog_item_image(_tenant_id(user), item))
@router.patch("/catalog/items/{item_id}")
async def update_catalog_item(
    item_id: UUID,
    req: CatalogItemUpdateRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "order")
    try:
        item = await _run_catalog_db(
            catalog_service.update_item,
            _tenant_id(user),
            item_id,
            req.model_dump(exclude_unset=True),
        )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="An active item with this SKU already exists") from exc
    if not item:
        raise HTTPException(status_code=404, detail="Catalog item or category not found")
    return APIResponse.create_success(data=await _with_catalog_item_image(_tenant_id(user), item))
@router.delete("/catalog/items/{item_id}")
async def delete_catalog_item(item_id: UUID, user: dict = Depends(require_tenant_admin)):
    await require_module_config_write(_tenant_id(user), "order")
    success = await _run_catalog_db(catalog_service.delete_item, _tenant_id(user), item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    return APIResponse.create_success(data={"deleted": True, "item_id": str(item_id)})
@router.post("/catalog/items/{item_id}/stock-adjustments")
async def adjust_catalog_stock(
    item_id: UUID,
    req: InventoryAdjustRequest,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "order")
    item = await _run_catalog_db(
        catalog_service.adjust_stock,
        _tenant_id(user),
        item_id,
        quantity_delta=req.quantity_delta,
        reason=req.reason,
        event_type=req.event_type,
        metadata=req.metadata,
        created_by=_user_id(user),
    )
    if not item:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    return APIResponse.create_success(data=_catalog_safe_record(item))
@router.get("/catalog/items/{item_id}/inventory-events")
async def list_inventory_events(
    item_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    item = await _run_catalog_db(catalog_service.get_item, _tenant_id(user), item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Catalog item not found")
    events = await _run_catalog_db(catalog_service.list_inventory_events, _tenant_id(user), item_id, limit=limit)
    return APIResponse.create_success(data=_catalog_safe_records(events))
