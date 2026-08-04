import hmac
import hashlib
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from backend.api.dependencies import (
    TENANT_WRITE_ROLES,
    require_tenant_admin,
    get_authenticated_user_only,
)
from backend.core.config import settings
from backend.db.client import db_client
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/api/v1/team", tags=["Team"])
MEMBER_ROLES = {"member", "operator", "tenant_admin", "admin", "owner"}
ADMIN_ROLES = {"tenant_admin", "admin", "owner"}
ROLE_HIERARCHY = {
    "member": 1,
    "operator": 2,
    "tenant_admin": 3,
    "admin": 4,
    "owner": 5,
    "super_admin": 6,
}
class InviteCreateRequest(BaseModel):
    email: EmailStr
    role: str = Field(pattern="^(member|operator|tenant_admin|admin|owner)$")
class InviteAcceptRequest(BaseModel):
    token: str = Field(min_length=1)
def hash_invite_token(token: str) -> str:
    secret_bytes = (settings.PUBLIC_CHAT_LINK_SECRET or "default-invite-pepper").encode("utf-8")
    token_bytes = token.encode("utf-8")
    return hmac.new(secret_bytes, token_bytes, hashlib.sha256).hexdigest()
class RoleUpdateRequest(BaseModel):
    role: str = Field(pattern="^(member|operator|tenant_admin|admin|owner)$")
    reason: str = Field(default="tenant_role_update", max_length=240)
class RemoveMemberRequest(BaseModel):
    reason: str = Field(default="tenant_member_removed", max_length=240)
@router.get("", response_model=APIResponse)
async def list_team_members(user: dict = Depends(require_tenant_admin)):
    tenant_id = user["tenant_id"]
    async with db_client.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id AS user_id, u.email, u.name, ut.role, ut.created_at
            FROM user_tenants ut
            JOIN users u ON u.id = ut.user_id AND u.deleted_at IS NULL
            WHERE ut.tenant_id = $1
            ORDER BY
              CASE WHEN ut.role IN ('owner', 'tenant_admin', 'admin') THEN 0 ELSE 1 END,
              ut.created_at ASC
            """,
            tenant_id,
        )
    return APIResponse.create_success(data={"members": [dict(row) for row in rows]})
@router.patch("/{member_user_id}/role", response_model=APIResponse)
async def update_member_role(
    member_user_id: UUID,
    body: RoleUpdateRequest,
    user: dict = Depends(require_tenant_admin),
):
    tenant_id = user["tenant_id"]
    actor_id = user["user_id"]
    actor_role = str(user.get("role") or "").lower()
    next_role = body.role.strip().lower()
    if next_role not in MEMBER_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if next_role in TENANT_WRITE_ROLES and actor_role not in TENANT_WRITE_ROLES:
        raise HTTPException(status_code=403, detail="Cannot grant that role")
    async with db_client.acquire() as conn:
        current = await conn.fetchrow(
            "SELECT role FROM user_tenants WHERE tenant_id = $1 AND user_id = $2",
            tenant_id,
            member_user_id,
        )
        if not current:
            raise HTTPException(status_code=404, detail="Team member not found")
        current_role = str(current["role"]).lower()
        if current_role in ADMIN_ROLES and next_role not in ADMIN_ROLES:
            await _ensure_not_last_admin(conn, tenant_id, member_user_id)
        if current_role == next_role:
            return APIResponse.create_success(data={"noop": True, "role": current_role})
        row = await conn.fetchrow(
            """
            UPDATE user_tenants
            SET role = $3
            WHERE tenant_id = $1 AND user_id = $2
            RETURNING user_id, tenant_id, role
            """,
            tenant_id,
            member_user_id,
            next_role,
        )
    await db_client.write_audit_log(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="tenant_member_role_updated",
        resource_type="user_tenant",
        resource_id=member_user_id,
        details={
            "member_user_id": str(member_user_id),
            "previous_role": current_role,
            "next_role": next_role,
            "reason": body.reason,
        },
    )
    return APIResponse.create_success(data={"noop": False, "member": dict(row)})
@router.delete("/{member_user_id}", response_model=APIResponse)
async def remove_team_member(
    member_user_id: UUID,
    body: RemoveMemberRequest | None = None,
    user: dict = Depends(require_tenant_admin),
):
    tenant_id = user["tenant_id"]
    actor_id = user["user_id"]
    if member_user_id == actor_id:
        raise HTTPException(status_code=400, detail="You cannot remove yourself from the tenant")
    reason = (body.reason if body else "tenant_member_removed")
    async with db_client.acquire() as conn:
        current = await conn.fetchrow(
            "SELECT role FROM user_tenants WHERE tenant_id = $1 AND user_id = $2",
            tenant_id,
            member_user_id,
        )
        if not current:
            raise HTTPException(status_code=404, detail="Team member not found")
        current_role = str(current["role"]).lower()
        if current_role in ADMIN_ROLES:
            await _ensure_not_last_admin(conn, tenant_id, member_user_id)
        deleted = await conn.fetchrow(
            """
            DELETE FROM user_tenants
            WHERE tenant_id = $1 AND user_id = $2
            RETURNING user_id, tenant_id, role
            """,
            tenant_id,
            member_user_id,
        )
    await db_client.write_audit_log(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="tenant_member_removed",
        resource_type="user_tenant",
        resource_id=member_user_id,
        details={
            "member_user_id": str(member_user_id),
            "previous_role": current_role,
            "reason": reason,
        },
    )
    return APIResponse.create_success(data={"removed": True, "member": dict(deleted)})
async def _ensure_not_last_admin(conn, tenant_id: UUID, member_user_id: UUID) -> None:
    remaining_admins = await conn.fetchval(
        """
        SELECT COUNT(*)
        FROM user_tenants
        WHERE tenant_id = $1
          AND user_id <> $2
          AND role IN ('tenant_admin', 'admin', 'owner')
        """,
        tenant_id,
        member_user_id,
    )
    if int(remaining_admins or 0) < 1:
        raise HTTPException(status_code=400, detail="Cannot remove or demote the last tenant admin")
@router.post("/invites", response_model=APIResponse)
async def create_invite(
    body: InviteCreateRequest,
    user: dict = Depends(require_tenant_admin),
):
    tenant_id = user["tenant_id"]
    actor_id = user["user_id"]
    actor_role = str(user.get("role") or "").lower()
    invited_role = body.role.strip().lower()
    email = str(body.email).strip().lower()
    if ROLE_HIERARCHY.get(invited_role, 0) > ROLE_HIERARCHY.get(actor_role, 0):
        raise HTTPException(
            status_code=403,
            detail="Cannot invite a user to a role higher than your own",
        )
    async with db_client.acquire() as conn:
        existing = await conn.fetchrow(
            """
            SELECT id FROM tenant_invitations
            WHERE tenant_id = $1 AND lower(email) = $2 AND status = 'pending'
            """,
            tenant_id,
            email,
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail="An active pending invitation already exists for this email",
            )
    import secrets
    from datetime import datetime, timezone, timedelta
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_invite_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    invite = await db_client.create_tenant_invitation(
        tenant_id=tenant_id,
        email=email,
        role=invited_role,
        token_hash=token_hash,
        invited_by_user_id=actor_id,
        expires_at=expires_at,
    )
    await db_client.write_audit_log(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="invite_created",
        resource_type="tenant_invitation",
        resource_id=invite["id"],
        details={
            "email": email,
            "role": invited_role,
            "expires_at": expires_at.isoformat(),
        },
    )
    expose_token = False
    if settings.ENV != "production":
        expose_token = True
    elif settings.ALLOW_MANUAL_INVITE_LINKS:
        expose_token = True
    response_data = {
        "invite_id": str(invite["id"]),
        "email": invite["email"],
        "role": invite["role"],
        "status": invite["status"],
        "expires_at": invite["expires_at"].isoformat(),
        "delivery_status": "not_configured",
    }
    if expose_token:
        response_data["token"] = raw_token
        response_data["invite_url"] = (
            f"{settings.AWA_PUBLIC_CHAT_BASE_URL}/accept-invite?token={raw_token}"
        )
    return APIResponse.create_success(data=response_data)
@router.get("/invites", response_model=APIResponse)
async def list_invites(user: dict = Depends(require_tenant_admin)):
    tenant_id = user["tenant_id"]
    invites = await db_client.get_tenant_invitations(tenant_id)
    return APIResponse.create_success(data={"invites": invites})
@router.post("/invites/{invite_id}/revoke", response_model=APIResponse)
async def revoke_invite(
    invite_id: UUID,
    user: dict = Depends(require_tenant_admin),
):
    tenant_id = user["tenant_id"]
    actor_id = user["user_id"]
    revoked = await db_client.revoke_tenant_invitation(tenant_id, invite_id)
    if not revoked:
        raise HTTPException(
            status_code=404,
            detail="Pending invitation not found",
        )
    await db_client.write_audit_log(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="invite_revoked",
        resource_type="tenant_invitation",
        resource_id=invite_id,
        details={
            "email": revoked["email"],
            "role": revoked["role"],
        },
    )
    return APIResponse.create_success(data={"revoked": True, "invite": revoked})
@router.post("/invites/accept", response_model=APIResponse)
async def accept_invite(
    body: InviteAcceptRequest,
    auth_user: dict = Depends(get_authenticated_user_only),
):
    token = body.token.strip()
    token_hash = hash_invite_token(token)
    invite = await db_client.get_tenant_invitation_by_hash(token_hash)
    if not invite:
        raise HTTPException(
            status_code=400,
            detail="Invalid, expired, or revoked invitation token",
        )
    if not hmac.compare_digest(invite["token_hash"], token_hash):
        raise HTTPException(
            status_code=400,
            detail="Invalid, expired, or revoked invitation token",
        )
    if invite["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail="Invalid, expired, or revoked invitation token",
        )
    from datetime import datetime, timezone
    if invite["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail="Invalid, expired, or revoked invitation token",
        )
    if invite["email"].strip().lower() != auth_user["email"].strip().lower():
        raise HTTPException(
            status_code=400,
            detail="Invalid, expired, or revoked invitation token",
        )
    tenant_id = invite["tenant_id"]
    invite_id = invite["id"]
    user_id = auth_user["user_id"]
    role = invite["role"]
    email = invite["email"]
    try:
        accepted = await db_client.accept_tenant_invitation(
            tenant_id=tenant_id,
            invite_id=invite_id,
            user_id=user_id,
            role=role,
            email=email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db_client.write_audit_log(
        tenant_id=tenant_id,
        actor_id=user_id,
        action="invite_accepted",
        resource_type="tenant_invitation",
        resource_id=invite_id,
        details={
            "email": email,
            "role": role,
        },
    )
    return APIResponse.create_success(
        data={
            "accepted": True,
            "tenant_id": str(tenant_id),
            "role": role,
        }
    )