from __future__ import annotations
import re
import socket
import ipaddress
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID
import httpx
from backend.core.config import settings, is_unsafe_url_for_production
from backend.ops.logger import logger

class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, resolved_ip: str, original_host: str, *args, **kwargs):
        self.resolved_ip = resolved_ip
        self.original_host = original_host
        super().__init__(*args, **kwargs)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request.extensions["sni_hostname"] = self.original_host
        request.headers["Host"] = self.original_host
        request.url = request.url.copy_with(host=self.resolved_ip)
        return await super().handle_async_request(request)

def _list_values(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
def build_support_notification_route(config: dict[str, Any]) -> dict[str, Any]:
    """
    Describe where support escalations are routed without exposing recipients.
    """
    modules = config.get("modules") if isinstance(config.get("modules"), dict) else {}
    support_config = modules.get("support") if isinstance(modules.get("support"), dict) else {}
    handoff_config = modules.get("human_handoff") if isinstance(modules.get("human_handoff"), dict) else {}
    notification_config = config.get("support_notifications")
    if not isinstance(notification_config, dict):
        notification_config = config.get("notifications") if isinstance(config.get("notifications"), dict) else {}
    channels = _list_values(notification_config.get("channels"))
    emails = (
        _list_values(notification_config.get("emails"))
        + _list_values(support_config.get("notification_emails"))
        + _list_values(handoff_config.get("notification_emails"))
    )
    phones = (
        _list_values(notification_config.get("phones"))
        + _list_values(support_config.get("notification_phones"))
        + _list_values(handoff_config.get("notification_phones"))
        + _list_values(config.get("escalation_phone"))
    )
    webhook_urls = _list_values(notification_config.get("webhook_urls"))
    recipient_count = len(set(emails + phones + webhook_urls))
    # We also consider platform-level webhook if no tenant-level is configured
    platform_webhook_active = (
        settings.SUPPORT_NOTIFICATION_MODE == "webhook"
        and bool(settings.SUPPORT_NOTIFICATION_WEBHOOK_URL)
    )
    external_configured = bool(channels or recipient_count or platform_webhook_active)
    return {
        "in_app_inbox": {
            "status": "routed",
            "route": "tenant_escalation_inbox",
        },
        "external_notification": {
            "status": "not_sent" if external_configured else "not_configured",
            "configured": external_configured,
            "channels": sorted(set(channels)),
            "recipient_count": recipient_count,
            "gap": (
                "External support notification delivery is not configured in this backend."
                if external_configured
                else "No external support notification recipients are configured."
            ),
        },
    }
async def record_support_notification_route(
    *,
    db_client: Any,
    tenant_id: UUID,
    actor_id: Optional[UUID],
    conversation_id: UUID,
    config: dict[str, Any],
    source: str,
    reason: str,
    urgency: str = "medium",
) -> dict[str, Any]:
    """
    Route conversation escalations externally.
    Option A: Awaited async delivery with a short timeout.
    """
    route = build_support_notification_route(config)
    # Write initial routed audit log
    await db_client.write_audit_log(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="support_notification_routed",
        resource_type="conversation",
        resource_id=conversation_id,
        details={
            "source": source,
            "reason": str(reason)[:120],
            "routing": {
                "in_app_inbox": dict(route["in_app_inbox"]),
                "external_notification": dict(route["external_notification"]),
            },
        },
    )
    # 1. Resolve target webhook URLs
    notification_config = config.get("support_notifications")
    if not isinstance(notification_config, dict):
        notification_config = config.get("notifications") if isinstance(config.get("notifications"), dict) else {}
    webhook_urls = _list_values(notification_config.get("webhook_urls"))
    # Fallback to platform-level webhook if no tenant-specific webhooks are defined
    using_platform_fallback = False
    if not webhook_urls:
        if settings.SUPPORT_NOTIFICATION_MODE == "webhook" and settings.SUPPORT_NOTIFICATION_WEBHOOK_URL:
            webhook_urls = [settings.SUPPORT_NOTIFICATION_WEBHOOK_URL]
            using_platform_fallback = True
    if not webhook_urls:
        # No webhook url is configured -> write skipped audit
        await db_client.write_audit_log(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="support_notification_skipped",
            resource_type="conversation",
            resource_id=conversation_id,
            details={
                "reason": "not_configured",
                "message": "No platform or tenant-level support webhooks are configured."
            }
        )
        route["external_notification"]["status"] = "skipped"
        return route
    # Query customer_id for this conversation to include in the payload
    customer_id = None
    try:
        async with db_client.acquire() as conn:
            customer_id = await conn.fetchval(
                "SELECT customer_id FROM conversations WHERE tenant_id = $1 AND id = $2",
                tenant_id, conversation_id
            )
    except Exception as db_exc:
        logger.warning("Failed to retrieve customer_id for conversation %s: %s", conversation_id, db_exc)
    # 2. Build sanitized payload
    payload: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "escalation_id": str(conversation_id),
        "conversation_id": str(conversation_id),
        "customer_id": str(customer_id) if customer_id else None,
        "reason": str(reason)[:240],
        "urgency": str(urgency).strip().lower(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if settings.AWA_PUBLIC_CHAT_BASE_URL:
        payload["dashboard_url"] = f"{settings.AWA_PUBLIC_CHAT_BASE_URL}/dashboard/conversations/{conversation_id}"
    # Construct message snippet if enabled
    if settings.SUPPORT_NOTIFICATION_INCLUDE_MESSAGE_SNIPPET:
        last_msg = None
        try:
            async with db_client.acquire() as conn:
                last_msg = await conn.fetchval(
                    """
                    SELECT text FROM messages
                    WHERE tenant_id = $1 AND conversation_id = $2 AND direction = 'inbound'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    tenant_id, conversation_id
                )
        except Exception as db_exc:
            logger.warning("Failed to retrieve last customer message for snippet: %s", db_exc)
        if last_msg:
            # Truncate aggressively (max 100 characters)
            snippet = str(last_msg)[:100]
            # Scrub tokens/secrets/bearer patterns
            snippet = re.sub(r"(mock_token|bearer\s+\S+|jwt\S+|password\S+)", "[REDACTED]", snippet, flags=re.IGNORECASE)
            payload["message_snippet"] = snippet
    delivery_status = "skipped"
    # 3. Deliver notifications
    for url in webhook_urls:
        # Resolve target IP for pinning & SSRF checking
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            original_host = parsed.hostname
            if not original_host:
                raise ValueError("Missing hostname")

            # Perform DNS resolution
            addr_info = socket.getaddrinfo(original_host, None)
            resolved_ips = [item[4][0] for item in addr_info]
            if not resolved_ips:
                raise ValueError("No IP addresses resolved")
            resolved_ip = resolved_ips[0]
        except Exception as exc:
            logger.warning("DNS resolution failed for webhook URL %s: %s", url, exc)
            await db_client.write_audit_log(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="support_notification_failed",
                resource_type="conversation",
                resource_id=conversation_id,
                details={
                    "url": url,
                    "reason": "dns_resolution_failed",
                    "message": str(exc),
                }
            )
            delivery_status = "failed"
            continue

        # Validate URL for SSRF protection
        is_unsafe = False
        reason_unsafe = "valid"
        if parsed.scheme not in ("http", "https"):
            is_unsafe, reason_unsafe = True, "unsafe_scheme"
        elif settings.ENV == "production" and parsed.scheme != "https":
            is_unsafe, reason_unsafe = True, "unsafe_scheme_production"
        elif parsed.username or parsed.password:
            is_unsafe, reason_unsafe = True, "credentials_in_url"
        else:
            # Check hostnames
            hostname_lower = original_host.lower().strip()
            if hostname_lower == "localhost":
                is_unsafe, reason_unsafe = True, "localhost_blocked"
            elif hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
                is_unsafe, reason_unsafe = True, "internal_domain_blocked"
            else:
                # Check resolved IP
                try:
                    ip = ipaddress.ip_address(resolved_ip)
                    if (
                        ip.is_private
                        or ip.is_loopback
                        or ip.is_link_local
                        or ip.is_multicast
                        or ip.is_reserved
                        or ip.is_unspecified
                    ):
                        is_unsafe, reason_unsafe = True, "private_or_local_ip_blocked"
                except ValueError:
                    is_unsafe, reason_unsafe = True, "invalid_ip_format"

        if is_unsafe:
            logger.warning("Skipping support notification webhook to %s: unsafe_url (%s)", url, reason_unsafe)
            await db_client.write_audit_log(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="support_notification_failed",
                resource_type="conversation",
                resource_id=conversation_id,
                details={
                    "url": url,
                    "reason": "unsafe_url",
                    "validation_error": reason_unsafe,
                }
            )
            delivery_status = "failed"
            continue
        # Log attempt
        await db_client.write_audit_log(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="support_notification_attempted",
            resource_type="conversation",
            resource_id=conversation_id,
            details={"url": url}
        )
        try:
            timeout_sec = float(settings.SUPPORT_NOTIFICATION_TIMEOUT_SECONDS)
            transport = PinnedAsyncHTTPTransport(resolved_ip=resolved_ip, original_host=original_host)
            async with httpx.AsyncClient(transport=transport, timeout=timeout_sec) as client:
                response = await client.post(url, json=payload)
            if 200 <= response.status_code < 300:
                await db_client.write_audit_log(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="support_notification_delivered",
                    resource_type="conversation",
                    resource_id=conversation_id,
                    details={
                        "url": url,
                        "status_code": response.status_code
                    }
                )
                if delivery_status != "failed":
                    delivery_status = "delivered"
            else:
                logger.warning("Webhook delivery failed with HTTP status %d", response.status_code)
                await db_client.write_audit_log(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action="support_notification_failed",
                    resource_type="conversation",
                    resource_id=conversation_id,
                    details={
                        "url": url,
                        "reason": "http_status",
                        "status_code": response.status_code
                    }
                )
                delivery_status = "failed"
        except httpx.TimeoutException:
            logger.warning("Webhook delivery timed out for URL %s", url)
            await db_client.write_audit_log(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="support_notification_failed",
                resource_type="conversation",
                resource_id=conversation_id,
                details={
                    "url": url,
                    "reason": "timeout"
                }
            )
            delivery_status = "failed"
        except httpx.RequestError as req_err:
            logger.warning("Webhook network error: %s", req_err)
            await db_client.write_audit_log(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="support_notification_failed",
                resource_type="conversation",
                resource_id=conversation_id,
                details={
                    "url": url,
                    "reason": "network_error",
                    "message": str(req_err)
                }
            )
            delivery_status = "failed"
        except Exception as exc:
            logger.warning("Webhook delivery exception: %s", exc)
            await db_client.write_audit_log(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action="support_notification_failed",
                resource_type="conversation",
                resource_id=conversation_id,
                details={
                    "url": url,
                    "reason": "provider_error",
                    "message": str(exc)
                }
            )
            delivery_status = "failed"
    route["external_notification"]["status"] = delivery_status
    return route
