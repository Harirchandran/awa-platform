"""
Privacy API Router — DPDP §12-13 / GDPR Art. 17, 20
Provides data erasure (Right to be Forgotten) and data export (Right to Portability).
"""
from fastapi import APIRouter, HTTPException, Depends
from uuid import UUID
from backend.db.client import db_client
from backend.ops.logger import logger
from backend.api.dependencies import get_current_user, require_tenant_admin
from backend.schemas.response import APIResponse
router = APIRouter(prefix="/privacy", tags=["Privacy & Compliance"])
async def write_privacy_audit(
    tenant_id: UUID,
    actor_id: UUID | None,
    action: str,
    customer_id: UUID,
    counts: dict,
    detail_key: str = "records_deleted",
) -> None:
    try:
        await db_client.write_audit_log(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type="customer",
            resource_id=customer_id,
            details={detail_key: counts},
            ip_address="system",
        )
    except Exception as audit_err:
        logger.warning(f"PRIVACY: Failed to write audit log | customer={customer_id} error={audit_err}")
def summarize_export_records(data: dict) -> dict[str, int]:
    summary: dict[str, int] = {}
    for key, value in data.items():
        if isinstance(value, list):
            summary[key] = len(value)
        elif isinstance(value, dict):
            summary[key] = 1 if value else 0
    return summary
# ==================== DATA ERASURE (Right to be Forgotten) ====================

@router.delete("/erase/{customer_id}", response_model=APIResponse)
async def schedule_customer_erasure(
    customer_id: UUID,
    user: dict = Depends(require_tenant_admin)
):
    """
    DPDP §12-13 / GDPR Art. 17: Schedule a customer data erasure (72-hour tombstone).

    Instead of an immediate hard-delete, this marks the customer as
    `pending_erasure` and sets `erasure_scheduled_at = NOW() + 72 hours`.
    The privacy reaper worker executes the actual cascade deletion once the
    lock window expires.
    """
    try:
        tenant_id = None
        if user.get("role") == "super_admin":
            async with db_client.acquire() as conn:
                tenant_id = await conn.fetchval(
                    "SELECT tenant_id FROM customers WHERE id = $1", customer_id
                )
            if not tenant_id:
                raise HTTPException(status_code=404, detail="Customer not found")
        else:
            tenant_id = (
                UUID(user.get("tenant_id"))
                if isinstance(user.get("tenant_id"), str)
                else user.get("tenant_id")
            )

        async with db_client.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE customers
                   SET status                = 'pending_erasure',
                       consent_status        = 'withdrawn',
                       erasure_scheduled_at  = NOW() + INTERVAL '72 hours',
                       updated_at            = NOW()
                 WHERE id = $1 AND tenant_id = $2
                RETURNING id, status, erasure_scheduled_at
                """,
                customer_id, tenant_id,
            )

        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")

        erasure_at = row["erasure_scheduled_at"]
        erasure_at_iso = erasure_at.isoformat() if hasattr(erasure_at, "isoformat") else str(erasure_at)

        await write_privacy_audit(
            tenant_id=tenant_id,
            actor_id=user.get("user_id"),
            action="erasure_scheduled",
            customer_id=customer_id,
            counts={"erasure_scheduled_at": erasure_at_iso},
        )
        logger.info(
            f"PRIVACY: Erasure scheduled | tenant={tenant_id} customer={customer_id} "
            f"scheduled_at={erasure_at_iso}"
        )
        return APIResponse.create_success(data={
            "action":               "erasure_scheduled",
            "customer_id":          str(customer_id),
            "erasure_scheduled_at": erasure_at_iso,
            "compliance":           ["DPDP §12", "GDPR Art. 17"],
            "note": (
                "Customer marked pending_erasure. Hard-delete will execute "
                "automatically after the 72-hour lock window expires."
            ),
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PRIVACY: Schedule erasure failed | customer={customer_id} error={e}")
        raise HTTPException(status_code=500, detail="Failed to schedule data erasure")


@router.post("/erase/{customer_id}/cancel", response_model=APIResponse)
async def cancel_customer_erasure(
    customer_id: UUID,
    user: dict = Depends(require_tenant_admin)
):
    """
    Cancel a pending erasure tombstone before the 72-hour window expires.
    Restores status to 'active' and clears erasure_scheduled_at.
    """
    try:
        tenant_id = None
        if user.get("role") == "super_admin":
            async with db_client.acquire() as conn:
                tenant_id = await conn.fetchval(
                    "SELECT tenant_id FROM customers WHERE id = $1", customer_id
                )
            if not tenant_id:
                raise HTTPException(status_code=404, detail="Customer not found")
        else:
            tenant_id = (
                UUID(user.get("tenant_id"))
                if isinstance(user.get("tenant_id"), str)
                else user.get("tenant_id")
            )

        async with db_client.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE customers
                   SET status               = 'active',
                       consent_status       = 'granted',
                       erasure_scheduled_at = NULL,
                       updated_at           = NOW()
                 WHERE id = $1 AND tenant_id = $2
                   AND status = 'pending_erasure'
                RETURNING id, status
                """,
                customer_id, tenant_id,
            )

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Customer not found or is not in pending_erasure status"
            )

        await write_privacy_audit(
            tenant_id=tenant_id,
            actor_id=user.get("user_id"),
            action="erasure_cancelled",
            customer_id=customer_id,
            counts={},
        )
        logger.info(
            f"PRIVACY: Erasure cancelled | tenant={tenant_id} customer={customer_id}"
        )
        return APIResponse.create_success(data={
            "action":      "erasure_cancelled",
            "customer_id": str(customer_id),
            "status":      "active",
            "compliance":  ["DPDP §12", "GDPR Art. 17"],
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PRIVACY: Cancel erasure failed | customer={customer_id} error={e}")
        raise HTTPException(status_code=500, detail="Failed to cancel erasure")
# ==================== ERASURE STATUS ====================

@router.get("/status/{customer_id}", response_model=APIResponse)
async def get_customer_privacy_status(
    customer_id: UUID,
    user: dict = Depends(require_tenant_admin)
):
    """
    Returns the customer's current status and erasure_scheduled_at.
    Used by the frontend to detect pending_erasure state on page load.
    """
    try:
        tenant_id = None
        if user.get("role") == "super_admin":
            async with db_client.acquire() as conn:
                tenant_id = await conn.fetchval(
                    "SELECT tenant_id FROM customers WHERE id = $1", customer_id
                )
            if not tenant_id:
                raise HTTPException(status_code=404, detail="Customer not found")
        else:
            tenant_id = (
                UUID(user.get("tenant_id"))
                if isinstance(user.get("tenant_id"), str)
                else user.get("tenant_id")
            )

        async with db_client.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, status, erasure_scheduled_at
                  FROM customers
                 WHERE id = $1 AND tenant_id = $2
                """,
                customer_id, tenant_id,
            )

        if not row:
            raise HTTPException(status_code=404, detail="Customer not found")

        ea = row["erasure_scheduled_at"]
        return APIResponse.create_success(data={
            "id":                   str(row["id"]),
            "status":               row["status"],
            "erasure_scheduled_at": ea.isoformat() if hasattr(ea, "isoformat") else ea,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PRIVACY: Status check failed | customer={customer_id} error={e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve customer status")


# ==================== DATA EXPORT (Right to Portability) ====================

@router.get("/export/{customer_id}", response_model=APIResponse)
async def export_customer_data(
    customer_id: UUID,
    user: dict = Depends(require_tenant_admin)
):
    """
    DPDP §11 / GDPR Art. 20: Export all customer data in structured JSON.
    """
    try:
        tenant_id = None
        if user.get("role") == "super_admin":
            async with db_client.acquire() as conn:
                tenant_id = await conn.fetchval("SELECT tenant_id FROM customers WHERE id = $1", customer_id)
            if not tenant_id:
                raise HTTPException(status_code=404, detail="Customer not found")
        else:
            tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
        data = await db_client.export_customer_data(tenant_id, customer_id)
        if data is None:
            raise HTTPException(status_code=404, detail="Customer not found")
        # Serialize UUIDs and datetimes to strings
        def serialize(obj):
            if isinstance(obj, dict):
                return {k: serialize(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [serialize(item) for item in obj]
            elif isinstance(obj, UUID):
                return str(obj)
            elif hasattr(obj, 'isoformat'):
                return obj.isoformat()
            return obj
        serialized_data = serialize(data)
        await write_privacy_audit(
            tenant_id=tenant_id,
            actor_id=user.get("user_id"),
            action="privacy_data_exported",
            customer_id=customer_id,
            counts=summarize_export_records(serialized_data),
            detail_key="records_exported",
        )
        logger.info(
            f"PRIVACY: Customer data exported | tenant={tenant_id} customer={customer_id}"
        )
        return APIResponse.create_success(data={
            "action": "data_exported",
            "compliance": ["DPDP §11", "GDPR Art. 20"],
            "export": serialized_data
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PRIVACY: Export failed | customer={customer_id} error={e}")
        raise HTTPException(status_code=500, detail="Data export failed")
# ==================== CONSENT MANAGEMENT ====================
@router.post("/consent/{customer_id}")
async def update_consent(
    customer_id: UUID,
    status: str = "granted",
    user: dict = Depends(require_tenant_admin)
):
    """
    DPDP §6 / GDPR Art. 7: Record consent grant or withdrawal.
    Valid status values: 'granted', 'withdrawn'
    """
    if status not in ("granted", "withdrawn"):
        raise HTTPException(status_code=400, detail="Status must be 'granted' or 'withdrawn'")
    try:
        tenant_id = None
        if user.get("role") == "super_admin":
            async with db_client.acquire() as conn:
                tenant_id = await conn.fetchval("SELECT tenant_id FROM customers WHERE id = $1", customer_id)
            if not tenant_id:
                raise HTTPException(status_code=404, detail="Customer not found")
        else:
            tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
        success = await db_client.update_customer_consent(
            tenant_id=tenant_id,
            customer_id=customer_id,
            consent_status=status
        )
        if not success:
            raise HTTPException(status_code=404, detail="Customer not found")
        logger.info(
            f"PRIVACY: Consent updated | tenant={tenant_id} customer={customer_id} status={status}"
        )
        # If consent withdrawn, trigger data erasure
        if status == "withdrawn":
            counts = await db_client.erase_customer_data(tenant_id, customer_id)
            await write_privacy_audit(
                tenant_id=tenant_id,
                actor_id=user.get("user_id"),
                action="privacy_consent_withdrawn_data_erased",
                customer_id=customer_id,
                counts=counts,
            )
            return APIResponse.create_success(data={
                "action": "consent_withdrawn_and_data_erased",
                "records_deleted": counts,
                "compliance": ["DPDP §6", "DPDP §12", "GDPR Art. 7", "GDPR Art. 17"]
            })
        return APIResponse.create_success(data={
            "action": f"consent_{status}",
            "customer_id": str(customer_id),
            "compliance": ["DPDP §6", "GDPR Art. 7"]
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"PRIVACY: Consent update failed | customer={customer_id} error={e}")
        raise HTTPException(status_code=500, detail="Consent update failed")