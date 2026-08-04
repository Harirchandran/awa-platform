from typing import Any
from uuid import UUID
import httpx
from fastapi import HTTPException
from backend.core.config import settings
def _auth_admin_headers() -> dict[str, str]:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(status_code=500, detail="Supabase admin authentication is not configured")
    return {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
async def create_supabase_auth_user(
    *,
    email: str,
    password: str,
    tenant_id: UUID,
    business_name: str,
) -> UUID:
    """Create the Supabase Auth identity that backs a tenant admin account."""
    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/admin/users"
    payload: dict[str, Any] = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"business_name": business_name},
        "app_metadata": {"tenant_id": str(tenant_id), "role": "tenant_admin"},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, headers=_auth_admin_headers(), json=payload)
    if response.status_code == 422:
        raise HTTPException(status_code=409, detail="An account with that email already exists")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not create authentication identity")
    data = response.json()
    user_id = data.get("id") or data.get("user", {}).get("id")
    if not user_id:
        raise HTTPException(status_code=502, detail="Authentication provider returned an invalid response")
    return UUID(user_id)
async def delete_supabase_auth_user(user_id: UUID) -> None:
    """Best-effort cleanup when DB registration fails after Auth user creation."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        return
    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/admin/users/{user_id}"
    async with httpx.AsyncClient(timeout=10) as client:
        await client.delete(url, headers=_auth_admin_headers())