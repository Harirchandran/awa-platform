"""
WhatsApp Accounts API Routes
GET /api/v1/whatsapp-accounts — list accounts
POST /api/v1/whatsapp-accounts — add new account
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.core.config import settings
from backend.db.client import db_client
from backend.integrations.meta_whatsapp import meta_whatsapp_client
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1", tags=["WhatsApp Accounts"])
class CreateAccountRequest(BaseModel):
    phone_number: str
    phone_number_id: Optional[str] = None
@router.get("/whatsapp-accounts")
async def list_accounts(user: dict = Depends(get_current_user)):
    """List WhatsApp accounts for the current tenant."""
    accounts = await db_client.list_whatsapp_accounts(user["tenant_id"])
    return APIResponse.create_success(data=accounts)
@router.post("/whatsapp-accounts")
async def create_account(
    body: CreateAccountRequest,
    user: dict = Depends(require_tenant_admin)
):
    """Add a new WhatsApp account."""
    if settings.effective_whatsapp_mode == "live":
        raise HTTPException(
            status_code=403,
            detail="Manual WhatsApp account creation is disabled in live mode. Use embedded signup.",
        )
    try:
        account = await db_client.create_whatsapp_account(
            user["tenant_id"], body.phone_number, body.phone_number_id
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return APIResponse.create_success(data=account)
class EmbeddedSignupRequest(BaseModel):
    code: str
    phone_number_id: Optional[str] = None
    waba_id: Optional[str] = None
    business_id: Optional[str] = None
@router.post("/whatsapp-accounts/embedded-signup")
async def process_embedded_signup(
    body: EmbeddedSignupRequest,
    user: dict = Depends(require_tenant_admin)
):
    """
    Process Meta Embedded Signup.
    In mock mode, creates a local simulated connection.
    In test/live modes, exchanges the Meta code, reads the selected phone
    number, subscribes the WABA to the app when possible, and stores the
    tenant-scoped credentials needed for webhook routing and outbound sends.
    """
    if settings.allow_mock_whatsapp_signup:
        mock_phone = f"+91 99999 {str(hash(user['tenant_id']))[-5:]}"
        mock_phone_id = body.phone_number_id or f"meta_{str(hash(user['tenant_id']))[-10:]}"
        try:
            account = await db_client.create_whatsapp_account(
                tenant_id=user["tenant_id"],
                phone_number=mock_phone,
                phone_number_id=mock_phone_id,
                provider_account_id=body.waba_id or "mock_waba",
                display_name="Mock WhatsApp Business",
                verification_status="connected",
                access_token="mock_whatsapp_access_token",
                waba_id=body.waba_id or "mock_waba",
                business_id=body.business_id,
                onboarding_mode="mock",
                app_id=settings.FACEBOOK_APP_ID,
            )
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return APIResponse.create_success(data=account)
    if settings.effective_whatsapp_mode not in {"test", "live"}:
        raise HTTPException(status_code=400, detail="WhatsApp integration mode is not enabled")
    result = await meta_whatsapp_client.complete_embedded_signup(
        code=body.code,
        phone_number_id=body.phone_number_id,
        waba_id=body.waba_id,
        business_id=body.business_id,
    )
    try:
        account = await db_client.create_whatsapp_account(
            tenant_id=user["tenant_id"],
            phone_number=result.phone_number,
            phone_number_id=result.phone_number_id,
            provider_account_id=result.waba_id,
            display_name=result.display_name,
            verification_status=result.verification_status,
            access_token=result.access_token,
            waba_id=result.waba_id,
            business_id=result.business_id,
            onboarding_mode=settings.effective_whatsapp_mode,
            app_id=settings.FACEBOOK_APP_ID,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return APIResponse.create_success(data=account)