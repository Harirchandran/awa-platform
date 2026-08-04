"""
Audit Helper — Convenience wrapper for audit log writes.
Only logs sensitive operations as specified in the user review.
"""
from uuid import UUID
from typing import Optional, Dict, Any
from backend.db.client import db_client
from backend.ops.logger import logger
# Sensitive operations that should be audit-logged
AUDITABLE_ACTIONS = {
    "config_created",
    "config_activated",
    "feature_flag_toggled",
    "data_erased",
    "consent_updated",
    "support_ticket_created",
    "tenant_created",
    "admin_action",
}
async def write_audit_log(
    tenant_id: UUID,
    actor_id: Optional[UUID],
    action: str,
    resource_type: str,
    resource_id: Optional[UUID] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Write an audit log entry for sensitive operations.
    Silently returns None if the action is not auditable.
    """
    if action not in AUDITABLE_ACTIONS:
        logger.warning(f"Non-auditable action '{action}' passed to audit helper — skipping")
        return None
    try:
        result = await db_client.write_audit_log(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address
        )
        return result
    except Exception as e:
        logger.error(f"Audit log write failed: {e}")
        return None