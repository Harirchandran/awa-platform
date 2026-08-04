from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID


ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class ValidatedImage:
    content: bytes
    original_filename_sanitized: str
    extension: str
    mime_type: str
    width: int
    height: int
    size_bytes: int


@dataclass(frozen=True)
class StoredImage:
    storage_key: str
    file_path: Path
    thumbnail_storage_key: Optional[str] = None
    thumbnail_path: Optional[Path] = None


@dataclass(frozen=True)
class MediaAttachRequest:
    tenant_id: UUID
    entity_type: str
    entity_id: UUID
    alt_text: Optional[str] = None
    caption: Optional[str] = None
