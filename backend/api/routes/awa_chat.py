import html
import hmac
import re
from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from backend.api.awa_chat_security import create_public_token, hash_public_chat_token
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.core.config import settings
from backend.db.client import db_client
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["AWA Chat"])
class CreateAwaChatLinkRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=80)
    slug: Optional[str] = Field(default=None, max_length=64)
class UpdateAwaChatLinkRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=80)
    status: Optional[str] = None
def _public_chat_url(slug: str) -> str:
    return f"{settings.AWA_PUBLIC_CHAT_BASE_URL.rstrip('/')}/chat/{slug}"
def _slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (base or "awa-chat")[:48].strip("-") or "awa-chat"
def _random_slug_part(token: str) -> str:
    safe_token = re.sub(r"[^a-z0-9]+", "", token.lower())
    return safe_token[-24:] or "chat-link"
def _json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
def _public_link_state(link: dict[str, Any]) -> dict[str, Any]:
    status = str(link.get("status") or "unknown")
    slug = str(link.get("slug") or "")
    token_hash = link.get("token_hash")
    if token_hash is not None:
        expected_hash = hash_public_chat_token(slug, "link")
        if not hmac.compare_digest(str(token_hash), expected_hash):
            return {
                "public_status": "needs_rotation",
                "is_publicly_accessible": False,
                "status_reason": "This link was created with an old or mismatched public-chat secret. Create a replacement QR/link before sharing it.",
            }
    if status != "active":
        return {
            "public_status": status,
            "is_publicly_accessible": False,
            "status_reason": "This public chat link is not active.",
        }
    return {
        "public_status": "active",
        "is_publicly_accessible": True,
        "status_reason": None,
    }
def _serialize_link(link: dict[str, Any]) -> dict[str, Any]:
    raw = dict(link)
    data = {
        key: _json_safe(value)
        for key, value in raw.items()
        if key not in {"token_hash", "metadata"}
    }
    if data.get("slug"):
        data["public_url"] = _public_chat_url(str(data["slug"]))
    data.update(_public_link_state(raw))
    return data
@router.get("/awa-chat/links")
async def list_awa_chat_links(user: dict = Depends(get_current_user)):
    links = await db_client.list_public_chat_links(user["tenant_id"])
    return APIResponse.create_success(data=[_serialize_link(link) for link in links])
@router.post("/awa-chat/links")
async def create_awa_chat_link(
    body: CreateAwaChatLinkRequest,
    user: dict = Depends(require_tenant_admin),
):
    tenant = await db_client.get_tenant(user["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    display_name = (body.display_name or tenant["name"] or "AWA Chat").strip()
    slug_base = _slugify(body.slug or display_name)[:40].strip("-") or "awa-chat"
    token = create_public_token("link")
    slug = f"{slug_base}-{_random_slug_part(token)}"
    token_hash = hash_public_chat_token(slug, "link")
    try:
        link = await db_client.create_public_chat_link(
            tenant_id=user["tenant_id"],
            slug=slug,
            display_name=display_name,
            token_hash=token_hash,
            metadata={"created_by": str(user["user_id"]), "token_binding": "slug"},
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Could not create AWA Chat link: {exc}")
    return APIResponse.create_success(data=_serialize_link(link))
@router.patch("/awa-chat/links/{link_id}")
async def update_awa_chat_link(
    link_id: UUID,
    body: UpdateAwaChatLinkRequest,
    user: dict = Depends(require_tenant_admin),
):
    if body.status and body.status not in {"active", "paused", "disabled"}:
        raise HTTPException(status_code=400, detail="Invalid link status")
    if body.status == "active":
        links = await db_client.list_public_chat_links(user["tenant_id"])
        current_link = next((link for link in links if link.get("id") == link_id), None)
        if not current_link:
            raise HTTPException(status_code=404, detail="AWA Chat link not found")
        current_state = _public_link_state(current_link)
        if current_state["public_status"] == "needs_rotation":
            raise HTTPException(
                status_code=409,
                detail="This AWA Chat link needs a replacement before customers can use it again.",
            )
    link = await db_client.update_public_chat_link(
        tenant_id=user["tenant_id"],
        link_id=link_id,
        display_name=body.display_name,
        status=body.status,
    )
    if not link:
        raise HTTPException(status_code=404, detail="AWA Chat link not found")
    return APIResponse.create_success(data=_serialize_link(link))
@router.post("/awa-chat/links/{link_id}/rotate")
async def rotate_awa_chat_link(
    link_id: UUID,
    user: dict = Depends(require_tenant_admin),
):
    links = await db_client.list_public_chat_links(user["tenant_id"])
    link = next((item for item in links if item["id"] == link_id), None)
    if not link:
        raise HTTPException(status_code=404, detail="AWA Chat link not found")
    display_name = link.get("display_name") or "AWA Chat"
    new_token = create_public_token("link")
    new_slug = f"{_slugify(display_name)}-{_random_slug_part(new_token)}"
    new_token_hash = hash_public_chat_token(new_slug, "link")
    updated = await db_client.rotate_public_chat_link(
        tenant_id=user["tenant_id"],
        link_id=link_id,
        new_slug=new_slug,
        new_token_hash=new_token_hash,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="AWA Chat link not found")
    return APIResponse.create_success(data=_serialize_link(updated))
@router.get("/awa-chat/links/{link_id}/qr")
async def get_awa_chat_link_qr(
    link_id: UUID,
    user: dict = Depends(get_current_user),
):
    links = await db_client.list_public_chat_links(user["tenant_id"])
    link = next((item for item in links if item["id"] == link_id), None)
    if not link:
        raise HTTPException(status_code=404, detail="AWA Chat link not found")
    public_url = _public_chat_url(link["slug"])
    try:
        import io
        import qrcode
        import qrcode.image.svg
        image = qrcode.make(public_url, image_factory=qrcode.image.svg.SvgPathImage)
        stream = io.BytesIO()
        image.save(stream)
        return Response(content=stream.getvalue(), media_type="image/svg+xml")
    except Exception:
        escaped_url = html.escape(public_url)
        escaped_name = html.escape(link.get("display_name") or "AWA Chat")
        fallback_svg = f"""
        <svg xmlns="http://www.w3.org/2000/svg" width="640" height="640" viewBox="0 0 640 640">
          <rect width="640" height="640" fill="#ffffff"/>
          <rect x="48" y="48" width="544" height="544" rx="16" fill="#f8fafc" stroke="#111827" stroke-width="4"/>
          <text x="320" y="250" font-family="Arial" font-size="34" font-weight="700" text-anchor="middle" fill="#111827">{escaped_name}</text>
          <text x="320" y="310" font-family="Arial" font-size="24" text-anchor="middle" fill="#334155">AWA Chat link</text>
          <text x="320" y="370" font-family="Arial" font-size="18" text-anchor="middle" fill="#475569">{escaped_url}</text>
        </svg>
        """
        return Response(content=fallback_svg, media_type="image/svg+xml")