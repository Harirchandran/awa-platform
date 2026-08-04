from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from backend.core.config import settings
from backend.domains.media.models import (
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_IMAGE_MIME_TYPES,
    MediaAttachRequest,
    StoredImage,
    ValidatedImage,
)
from backend.domains.media.repository import MediaRepository, media_repository


ENTITY_TYPES = {"catalog_item", "service", "category"}
IMAGE_FORMAT_TO_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


def sanitize_media_record(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not record:
        return None
    return {
        "id": str(record.get("id")),
        "url": record.get("served_url"),
        "thumbnail_url": record.get("thumbnail_url"),
        "alt_text": record.get("alt_text"),
        "caption": record.get("caption"),
        "width": record.get("width"),
        "height": record.get("height"),
        "mime_type": record.get("mime_type"),
        "size_bytes": record.get("size_bytes"),
    }


def sanitize_media_filename(filename: str | None) -> str:
    raw = Path(filename or "image").name
    stem = Path(raw).stem[:80] or "image"
    suffix = Path(raw).suffix.lower()
    safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip(".-_") or "image"
    safe_suffix = suffix if suffix in ALLOWED_IMAGE_EXTENSIONS else ""
    return f"{safe_stem}{safe_suffix}"


class LocalMediaStorage:
    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir).resolve()

    def _path_for_key(self, storage_key: str) -> Path:
        path = (self.root_dir / storage_key).resolve()
        if self.root_dir not in path.parents and path != self.root_dir:
            raise ValueError("media storage path escaped root")
        return path

    def save(self, tenant_id: UUID, entity_type: str, entity_id: UUID, image: ValidatedImage) -> StoredImage:
        ext = image.extension
        filename = f"{uuid4().hex}{ext}"
        storage_key = f"tenants/{tenant_id}/{entity_type}/{entity_id}/{filename}"
        file_path = self._path_for_key(storage_key)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(image.content)
        thumbnail_key = f"tenants/{tenant_id}/{entity_type}/{entity_id}/thumb-{filename}"
        thumbnail_path = self._path_for_key(thumbnail_key)
        self._write_thumbnail(image.content, thumbnail_path)
        return StoredImage(
            storage_key=storage_key,
            file_path=file_path,
            thumbnail_storage_key=thumbnail_key,
            thumbnail_path=thumbnail_path,
        )

    def delete_if_present(self, storage_key: str | None) -> None:
        if not storage_key:
            return
        path = self._path_for_key(storage_key)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def _write_thumbnail(self, content: bytes, thumbnail_path: Path) -> None:
        thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(BytesIO(content)) as image:
            image.thumbnail((480, 480))
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            image.save(thumbnail_path, format=image.format or "JPEG")


class MediaService:
    def __init__(
        self,
        repository: MediaRepository = media_repository,
        storage: Optional[LocalMediaStorage] = None,
    ):
        self.repository = repository
        self.storage = storage or LocalMediaStorage(settings.MEDIA_STORAGE_DIR)

    async def validate_upload(self, upload: UploadFile) -> ValidatedImage:
        original_filename = sanitize_media_filename(upload.filename)
        extension = Path(original_filename).suffix.lower()
        if extension not in ALLOWED_IMAGE_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Unsupported image extension")
        header_mime = (upload.content_type or "").split(";")[0].strip().lower()
        if header_mime not in ALLOWED_IMAGE_MIME_TYPES:
            raise HTTPException(status_code=400, detail="Unsupported image type")
        max_bytes = int(settings.MEDIA_MAX_UPLOAD_BYTES or (5 * 1024 * 1024))
        content = await upload.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise HTTPException(status_code=413, detail="Image is too large")
        if not content:
            raise HTTPException(status_code=400, detail="Image is empty")
        try:
            with Image.open(BytesIO(content)) as image:
                image.verify()
            with Image.open(BytesIO(content)) as image:
                width, height = image.size
                decoded_mime = IMAGE_FORMAT_TO_MIME.get((image.format or "").upper())
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid image") from exc
        if decoded_mime != header_mime:
            raise HTTPException(status_code=400, detail="Image MIME type does not match file content")
        return ValidatedImage(
            content=content,
            original_filename_sanitized=original_filename,
            extension=extension,
            mime_type=decoded_mime,
            width=width,
            height=height,
            size_bytes=len(content),
        )

    async def attach_catalog_item_image(
        self,
        request: MediaAttachRequest,
        upload: UploadFile,
    ) -> Dict[str, Any]:
        if request.entity_type != "catalog_item":
            raise HTTPException(status_code=400, detail="Unsupported media entity type")
        exists = await self.repository.catalog_item_exists(request.tenant_id, request.entity_id)
        if not exists:
            raise HTTPException(status_code=404, detail="Catalog item not found")
        image = await self.validate_upload(upload)
        asset_id = uuid4()
        stored = self.storage.save(request.tenant_id, request.entity_type, request.entity_id, image)
        served_url = f"/api/v1/media/assets/{asset_id}/file"
        thumbnail_url = f"/api/v1/media/assets/{asset_id}/thumbnail"
        record = await self.repository.create_primary_asset(
            asset_id=asset_id,
            tenant_id=request.tenant_id,
            entity_type=request.entity_type,
            entity_id=request.entity_id,
            storage_key=stored.storage_key,
            served_url=served_url,
            thumbnail_url=thumbnail_url,
            original_filename_sanitized=image.original_filename_sanitized,
            mime_type=image.mime_type,
            width=image.width,
            height=image.height,
            size_bytes=image.size_bytes,
            alt_text=request.alt_text,
            caption=request.caption,
        )
        return record

    async def delete_catalog_item_image(self, tenant_id: UUID, item_id: UUID, image_id: UUID) -> Dict[str, Any]:
        if not await self.repository.catalog_item_exists(tenant_id, item_id):
            raise HTTPException(status_code=404, detail="Catalog item not found")
        deleted = await self.repository.mark_asset_deleted(tenant_id, "catalog_item", item_id, image_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Image not found")
        self.storage.delete_if_present(deleted.get("storage_key"))
        return {"deleted": True, "image_id": str(image_id), "item_id": str(item_id)}

    async def get_asset_file_path(self, asset_id: UUID, thumbnail: bool = False) -> tuple[Path, str]:
        asset = await self.repository.get_asset(asset_id)
        if not asset:
            raise HTTPException(status_code=404, detail="Image not found")
        key = asset.get("storage_key")
        if thumbnail:
            key_path = Path(str(key))
            key = str(key_path.with_name(f"thumb-{key_path.name}"))
        path = self.storage._path_for_key(str(key))
        if not path.exists():
            raise HTTPException(status_code=404, detail="Image file not found")
        return path, str(asset.get("mime_type") or "application/octet-stream")


media_service = MediaService()
