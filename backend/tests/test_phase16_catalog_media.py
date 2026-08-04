from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from backend.domains.media.models import MediaAttachRequest
from backend.domains.media.service import LocalMediaStorage, MediaService, sanitize_media_record
from backend.pipeline.channel_output import extract_safe_cards, normalize_channel_payload, redact_url_tokens
from backend.api.whatsapp_client import WhatsAppClient


def image_upload(fmt: str = "JPEG", filename: str = "kit.jpg", content_type: str = "image/jpeg") -> UploadFile:
    stream = BytesIO()
    Image.new("RGB", (24, 16), "white").save(stream, format=fmt)
    stream.seek(0)
    return UploadFile(filename=filename, file=stream, headers={"content-type": content_type})


class FakeMediaRepository:
    def __init__(self, owner_tenant, item_id):
        self.owner_tenant = owner_tenant
        self.item_id = item_id
        self.assets = {}
        self.deleted_items = set()

    async def catalog_item_exists(self, tenant_id, item_id):
        return tenant_id == self.owner_tenant and item_id == self.item_id

    async def create_primary_asset(self, **kwargs):
        for existing in self.assets.values():
            if (
                existing["tenant_id"] == kwargs["tenant_id"]
                and existing["entity_type"] == kwargs["entity_type"]
                and existing["entity_id"] == kwargs["entity_id"]
            ):
                existing["status"] = "hidden"
                existing["is_primary"] = False
        record = {
            **kwargs,
            "id": kwargs["asset_id"],
            "status": "active",
            "is_primary": True,
            "created_at": "now",
            "updated_at": "now",
        }
        self.assets[kwargs["asset_id"]] = record
        return record

    async def list_primary_assets(self, tenant_id, entity_type, entity_ids):
        return {
            str(asset["entity_id"]): asset
            for asset in self.assets.values()
            if asset["tenant_id"] == tenant_id
            and asset["entity_type"] == entity_type
            and asset["entity_id"] in entity_ids
            and asset["status"] == "active"
            and asset["is_primary"]
        }

    async def mark_asset_deleted(self, tenant_id, entity_type, entity_id, asset_id):
        asset = self.assets.get(asset_id)
        if not asset or asset["tenant_id"] != tenant_id or asset["entity_id"] != entity_id:
            return None
        asset["status"] = "deleted"
        asset["is_primary"] = False
        return asset


@pytest.mark.asyncio
async def test_valid_jpeg_png_webp_uploads_are_accepted(tmp_path):
    tenant_id = uuid4()
    item_id = uuid4()
    repo = FakeMediaRepository(tenant_id, item_id)
    service = MediaService(repository=repo, storage=LocalMediaStorage(str(tmp_path)))
    for fmt, filename, mime in [
        ("JPEG", "kit.jpg", "image/jpeg"),
        ("PNG", "kit.png", "image/png"),
        ("WEBP", "kit.webp", "image/webp"),
    ]:
        record = await service.attach_catalog_item_image(
            MediaAttachRequest(tenant_id=tenant_id, entity_type="catalog_item", entity_id=item_id, alt_text="Dental kit"),
            image_upload(fmt, filename, mime),
        )
        safe = sanitize_media_record(record)
        assert safe["url"].startswith("/api/v1/media/assets/")
        assert safe["thumbnail_url"].startswith("/api/v1/media/assets/")
        assert safe["alt_text"] == "Dental kit"
        assert "storage_key" not in safe


@pytest.mark.asyncio
async def test_invalid_file_type_and_oversized_upload_rejected(monkeypatch, tmp_path):
    tenant_id = uuid4()
    item_id = uuid4()
    service = MediaService(repository=FakeMediaRepository(tenant_id, item_id), storage=LocalMediaStorage(str(tmp_path)))
    with pytest.raises(HTTPException) as bad_ext:
        await service.validate_upload(image_upload("PNG", "bad.gif", "image/png"))
    assert bad_ext.value.status_code == 400
    monkeypatch.setattr("backend.domains.media.service.settings.MEDIA_MAX_UPLOAD_BYTES", 10)
    with pytest.raises(HTTPException) as too_big:
        await service.validate_upload(image_upload())
    assert too_big.value.status_code == 413


@pytest.mark.asyncio
async def test_tenant_cannot_attach_image_to_other_tenant_catalog_item(tmp_path):
    owner_tenant = uuid4()
    attacker_tenant = uuid4()
    item_id = uuid4()
    service = MediaService(repository=FakeMediaRepository(owner_tenant, item_id), storage=LocalMediaStorage(str(tmp_path)))
    with pytest.raises(HTTPException) as exc:
        await service.attach_catalog_item_image(
            MediaAttachRequest(tenant_id=attacker_tenant, entity_type="catalog_item", entity_id=item_id),
            image_upload(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_media_metadata_is_tenant_scoped_and_delete_does_not_delete_item(tmp_path):
    tenant_id = uuid4()
    other_tenant = uuid4()
    item_id = uuid4()
    repo = FakeMediaRepository(tenant_id, item_id)
    service = MediaService(repository=repo, storage=LocalMediaStorage(str(tmp_path)))
    record = await service.attach_catalog_item_image(
        MediaAttachRequest(tenant_id=tenant_id, entity_type="catalog_item", entity_id=item_id),
        image_upload(),
    )
    assert await repo.list_primary_assets(other_tenant, "catalog_item", [item_id]) == {}
    assert str(item_id) in await repo.list_primary_assets(tenant_id, "catalog_item", [item_id])
    result = await service.delete_catalog_item_image(tenant_id, item_id, record["asset_id"])
    assert result["deleted"] is True
    assert await repo.catalog_item_exists(tenant_id, item_id) is True


def test_llm_context_and_transcript_redaction_do_not_expose_private_media():
    payload = {
        "success": True,
        "user_message": "Here is the dental kit.",
        "data": {
            "items": [
                {
                    "id": "item-1",
                    "name": "Dental Kit",
                    "has_image": True,
                    "image_alt": "Dental kit box",
                }
            ]
        },
        "cards": [
            {
                "type": "catalog_item",
                "id": "item-1",
                "title": "Dental Kit",
                "subtitle": "INR 350",
                "description": "Daily care bundle",
                "image": {
                    "url": "/api/v1/media/assets/11111111-1111-1111-1111-111111111111/file",
                    "thumbnail_url": "/api/v1/media/assets/11111111-1111-1111-1111-111111111111/thumbnail",
                    "alt_text": "Dental kit box",
                },
                "storage_key": "tenants/private/key.jpg",
                "actions": ["order", "ask_more"],
            }
        ],
    }
    normalized = normalize_channel_payload(payload, "awa_chat")
    assert normalized["text"] == "Here is the dental kit."
    assert normalized["cards"][0]["image_url"].startswith("/api/v1/media/assets/")
    assert "storage_key" not in normalized["cards"][0]
    redacted = redact_url_tokens({"url": "https://cdn.example.test/image.jpg?token=secret"})
    assert redacted["url"].endswith("?[redacted]")


def test_awa_chat_card_attachment_shape_and_cross_tenant_leak_guard():
    cards = extract_safe_cards({
        "cards": [
            {"type": "catalog_item", "title": "Kit", "image_url": "/api/v1/media/assets/a/file"},
            {"type": "catalog_item", "title": "Bad", "image_url": "file://localhost/private.jpg"},
        ]
    })
    assert cards[0]["title"] == "Kit"
    assert cards[0]["image_url"].startswith("/api/v1/media/assets/")
    assert cards[1]["image_url"] == ""


@pytest.mark.asyncio
async def test_whatsapp_image_caption_or_fallback(monkeypatch):
    monkeypatch.setattr("backend.api.whatsapp_client.settings.AWA_INTEGRATION_MODE", "mock")
    monkeypatch.setattr("backend.api.whatsapp_client.settings.WHATSAPP_MODE", "mock")
    client = WhatsAppClient()
    result = await client.send_image_message("1234567890", "https://example.test/kit.jpg", "Dental Kit\nINR 350")
    assert result["success"] is True
    assert result["message_id"].startswith("mock_whatsapp_image_")
