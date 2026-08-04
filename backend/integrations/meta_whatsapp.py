from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional
import httpx
from fastapi import HTTPException
from backend.core.config import settings
@dataclass(frozen=True)
class EmbeddedSignupResult:
    access_token: str
    phone_number_id: str
    phone_number: str
    display_name: Optional[str]
    waba_id: Optional[str]
    business_id: Optional[str]
    verification_status: str
    raw_payload: dict[str, Any]
class MetaWhatsAppClient:
    """Small Graph API client for WhatsApp Embedded Signup provisioning."""
    def __init__(self, graph_version: str | None = None):
        self.graph_version = graph_version or settings.WHATSAPP_GRAPH_API_VERSION
        self.base_url = f"https://graph.facebook.com/{self.graph_version}"
    async def complete_embedded_signup(
        self,
        code: str,
        phone_number_id: Optional[str] = None,
        waba_id: Optional[str] = None,
        business_id: Optional[str] = None,
    ) -> EmbeddedSignupResult:
        self._require_meta_settings()
        token = await self.exchange_code_for_token(code)
        resolved_waba_id = waba_id
        resolved_phone_number_id = phone_number_id
        if not resolved_phone_number_id and resolved_waba_id:
            phone_numbers = await self.list_phone_numbers(resolved_waba_id, token)
            if phone_numbers:
                resolved_phone_number_id = phone_numbers[0].get("id")
        if not resolved_phone_number_id:
            raise HTTPException(
                status_code=400,
                detail="Embedded signup did not return a WhatsApp phone number ID",
            )
        phone_info = await self.get_phone_number(resolved_phone_number_id, token)
        if resolved_waba_id:
            await self.subscribe_waba(resolved_waba_id, token)
        display_phone = phone_info.get("display_phone_number")
        if not display_phone:
            raise HTTPException(status_code=502, detail="Meta did not return display_phone_number")
        return EmbeddedSignupResult(
            access_token=token,
            phone_number_id=resolved_phone_number_id,
            phone_number=display_phone,
            display_name=phone_info.get("verified_name"),
            waba_id=resolved_waba_id,
            business_id=business_id,
            verification_status=_map_verification_status(phone_info),
            raw_payload={"phone_number": phone_info},
        )
    async def exchange_code_for_token(self, code: str) -> str:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.base_url}/oauth/access_token",
                params={
                    "client_id": settings.FACEBOOK_APP_ID,
                    "client_secret": settings.FACEBOOK_APP_SECRET,
                    "code": code,
                },
            )
        data = _decode_meta_response(response)
        token = data.get("access_token")
        if not token:
            raise HTTPException(status_code=502, detail="Meta token exchange did not return access_token")
        return token
    async def get_phone_number(self, phone_number_id: str, access_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.base_url}/{phone_number_id}",
                params={
                    "fields": "id,display_phone_number,verified_name,quality_rating,code_verification_status",
                    "access_token": access_token,
                },
            )
        return _decode_meta_response(response)
    async def list_phone_numbers(self, waba_id: str, access_token: str) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{self.base_url}/{waba_id}/phone_numbers",
                params={
                    "fields": "id,display_phone_number,verified_name,quality_rating,code_verification_status",
                    "access_token": access_token,
                },
            )
        data = _decode_meta_response(response)
        numbers = data.get("data", [])
        return numbers if isinstance(numbers, list) else []
    async def subscribe_waba(self, waba_id: str, access_token: str) -> None:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.base_url}/{waba_id}/subscribed_apps",
                params={"access_token": access_token},
            )
        _decode_meta_response(response)
    def _require_meta_settings(self) -> None:
        missing = [
            name
            for name, value in {
                "FACEBOOK_APP_ID": settings.FACEBOOK_APP_ID,
                "FACEBOOK_APP_SECRET": settings.FACEBOOK_APP_SECRET,
            }.items()
            if not value
        ]
        if missing:
            raise HTTPException(status_code=500, detail=f"Missing Meta settings: {', '.join(missing)}")
def _decode_meta_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}
    if response.is_error:
        if isinstance(data, dict):
            error = data.get("error", data)
        else:
            error = data
        raise HTTPException(status_code=502, detail={"meta_error": error})
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Unexpected Meta response")
    return data
def _map_verification_status(phone_info: dict[str, Any]) -> str:
    code_status = str(phone_info.get("code_verification_status") or "").upper()
    if code_status in {"VERIFIED", "EXPIRED"}:
        return "verified" if code_status == "VERIFIED" else "pending"
    return "connected"
meta_whatsapp_client = MetaWhatsAppClient()