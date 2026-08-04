from typing import Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from backend.api.entitlements import require_module_config_write
from backend.api.dependencies import require_tenant_admin
from backend.domains.media.models import MediaAttachRequest
from backend.domains.media.service import media_service, sanitize_media_record
from backend.schemas.response import APIResponse

router = APIRouter(prefix="/api/v1/media", tags=["Media"])


def _tenant_id(user: dict) -> UUID:
    return UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")


async def _run_media_operation(operation, *args, **kwargs):
    try:
        return await operation(*args, **kwargs)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError) as exc:
        raise HTTPException(status_code=503, detail="Media storage schema is unavailable. Run migrations first.") from exc


@router.post("/catalog-items/{item_id}/image")
async def upload_catalog_item_image(
    item_id: UUID,
    file: UploadFile = File(...),
    alt_text: Optional[str] = Form(default=None, max_length=300),
    caption: Optional[str] = Form(default=None, max_length=500),
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "order")
    record = await _run_media_operation(
        media_service.attach_catalog_item_image,
        MediaAttachRequest(
            tenant_id=_tenant_id(user),
            entity_type="catalog_item",
            entity_id=item_id,
            alt_text=alt_text,
            caption=caption,
        ),
        file,
    )
    return APIResponse.create_success(data=sanitize_media_record(record))


@router.delete("/catalog-items/{item_id}/image/{image_id}")
async def delete_catalog_item_image(
    item_id: UUID,
    image_id: UUID,
    user: dict = Depends(require_tenant_admin),
):
    await require_module_config_write(_tenant_id(user), "order")
    result = await _run_media_operation(media_service.delete_catalog_item_image, _tenant_id(user), item_id, image_id)
    return APIResponse.create_success(data=result)


@router.get("/assets/{asset_id}/file")
async def get_media_asset_file(asset_id: UUID):
    path, mime_type = await _run_media_operation(media_service.get_asset_file_path, asset_id, False)
    return FileResponse(path, media_type=mime_type)


@router.get("/assets/{asset_id}/thumbnail")
async def get_media_asset_thumbnail(asset_id: UUID):
    path, mime_type = await _run_media_operation(media_service.get_asset_file_path, asset_id, True)
    return FileResponse(path, media_type=mime_type)
