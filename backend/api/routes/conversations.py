"""
Conversation API Routes
GET /api/v1/conversations — list conversations with cursor pagination
GET /api/v1/conversations/{id}/messages — list messages for a conversation
"""
from datetime import datetime, timedelta, timezone
import json
from typing import Optional
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Query, HTTPException
from backend.api.dependencies import get_current_user, require_tenant_operator
from backend.api.entitlements import require_any_module_operational_write, require_module_config_write
from backend.api.pagination import decode_cursor
from backend.api.whatsapp_client import whatsapp_client
from backend.ai.rag_service import rag_service
from backend.core.states import ConversationState
from backend.db.client import db_client
from backend.domains.support.service import support_service
from backend.pipeline.session_manager import session_manager
from backend.schemas.response import APIResponse
from pydantic import BaseModel, Field, field_validator
router = APIRouter(prefix="/api/v1", tags=["Conversations"])
CONVERSATION_STATUSES = {"active", "closed", "escalated"}
class UpdateConversationRequest(BaseModel):
    status: str
class EscalationActionRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)
class EscalationReplyRequest(BaseModel):
    message_text: str = Field(min_length=1, max_length=2000)
    save_to_knowledge: bool = False
    close_after_reply: bool = False
    @field_validator("message_text")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Reply cannot be empty")
        return value
PUBLIC_CHAT_REPLY_STALE_AFTER = timedelta(minutes=30)
def _coerce_state_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}
async def _set_session_state(
    tenant_id: UUID,
    conversation_id: UUID,
    workflow_state: ConversationState,
    clear_module: bool = True,
) -> None:
    session = await db_client.get_conversation_session(tenant_id, conversation_id)
    if not session:
        return
    state_json = _coerce_state_json(session.get("state_json"))
    state_data = state_json.get("state") if isinstance(state_json.get("state"), dict) else {}
    state_data["workflow_state"] = workflow_state.value
    state_data["last_updated_at"] = datetime.now(timezone.utc).isoformat()
    if clear_module:
        state_data["step_data"] = {}
        state_data["agentic_history"] = []
        state_data["action_ledger"] = {
            "current_task": None,
            "task_status": "none",
            "pending_action": None,
            "last_tool_result": None,
            "channel": "awa_chat",
        }
    await db_client.update_conversation_session(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        state_json={
            "config_version": session["config_version"],
            "state": state_data,
        },
        config_version=session["config_version"],
        current_module=None if clear_module else session.get("current_module"),
        current_intent=None if clear_module else session.get("current_intent"),
    )
    await session_manager.invalidate_cache(tenant_id, conversation_id)
def _tenant_id_from_user(user: dict) -> UUID:
    return UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
async def _save_reply_to_knowledge(
    tenant_id: UUID,
    conversation: dict,
    reply_text: str,
    actor_id: UUID,
) -> Optional[dict]:
    reason = (conversation.get("escalation_reason") or "Customer escalation").strip()
    customer = conversation.get("customer_name") or conversation.get("customer_phone") or "Customer"
    chunk_text = (
        f"Escalation reason/question: {reason}\n"
        f"Tenant answer: {reply_text}"
    )
    embedding = await rag_service.embed_query(chunk_text)
    return await support_service.create_knowledge_base_entry(
        tenant_id=tenant_id,
        document_title=f"Escalation answer - {customer}",
        chunk_text=chunk_text,
        embedding=embedding,
        metadata={
            "source": "tenant_escalation_reply",
            "conversation_id": str(conversation["id"]),
            "customer_id": str(conversation["customer_id"]),
            "actor_id": str(actor_id),
        },
    )
async def _save_outbound_reply(
    tenant_id: UUID,
    conversation_id: UUID,
    message_text: str,
    channel_type: str,
    provider_message_id: str,
    raw_payload: dict,
    delivery_status: str,
) -> Optional[UUID]:
    msg_id = await support_service.save_human_reply(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        provider_message_id=provider_message_id,
        channel_type=channel_type,
        message_text=message_text,
        raw_payload=raw_payload,
        delivery_status=delivery_status,
    )
    from uuid import UUID as UUID_Type
    if channel_type == "awa_chat" and isinstance(msg_id, UUID_Type):
        try:
            from backend.realtime.public_chat_events import publish_public_chat_message_event
            await publish_public_chat_message_event(
                conversation_id=conversation_id,
                message_id=msg_id,
                content=message_text,
                raw_payload=raw_payload,
            )
        except Exception as e:
            logger.error(f"Failed to publish public chat human reply event: {e}")
    return msg_id if isinstance(msg_id, UUID_Type) else None
def _public_session_is_reachable(session: Optional[dict]) -> bool:
    if not session or session.get("status") != "active":
        return False
    last_seen = session.get("last_seen_at") or session.get("updated_at")
    if not isinstance(last_seen, datetime):
        return True
    return datetime.now(timezone.utc) - last_seen <= PUBLIC_CHAT_REPLY_STALE_AFTER
@router.get("/conversations")
async def list_conversations(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status (e.g. active)"),
    search: Optional[str] = Query(None, description="Search by customer name or phone"),
    user: dict = Depends(get_current_user)
):
    """List conversations for the current tenant with cursor pagination and optional filters."""
    if status and status not in CONVERSATION_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid conversation status")
    decoded = decode_cursor(cursor) if cursor else None
    result = await db_client.list_conversations(
        user["tenant_id"], cursor=decoded, limit=limit,
        status=status, search=search
    )
    return APIResponse.create_success(data=result["items"], meta={"next_cursor": result["next_cursor"]})
@router.get("/escalations")
async def list_escalations(
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(30, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by customer name or phone"),
    user: dict = Depends(get_current_user)
):
    """List currently escalated conversations for the tenant inbox."""
    decoded = decode_cursor(cursor) if cursor else None
    result = await support_service.list_escalated_conversations(
        user["tenant_id"], cursor=decoded, limit=limit, search=search
    )
    return APIResponse.create_success(data=result["items"], meta={"next_cursor": result["next_cursor"]})
@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    user: dict = Depends(get_current_user)
):
    """Get a specific conversation for the current tenant."""
    conversation = await db_client.get_conversation(user["tenant_id"], conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return APIResponse.create_success(data=conversation)
@router.post("/conversations/{conversation_id}/escalation")
async def escalate_conversation(
    conversation_id: UUID,
    req: EscalationActionRequest,
    user: dict = Depends(require_tenant_operator)
):
    """Manually pause automation and move a conversation into the escalation inbox."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    await require_any_module_operational_write(tenant_id, ("support", "human_handoff"))
    conversation = await support_service.escalate_conversation(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        reason=req.reason or "Tenant manually escalated this conversation",
        assigned_to=user.get("user_id"),
        resume_mode="tenant",
        source="operator",
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _set_session_state(tenant_id, conversation_id, ConversationState.ESCALATED)
    return APIResponse.create_success(data=conversation)
@router.post("/conversations/{conversation_id}/escalation/reply")
async def reply_to_escalation(
    conversation_id: UUID,
    req: EscalationReplyRequest,
    user: dict = Depends(require_tenant_operator)
):
    """Send a tenant human reply while AWA automation remains paused."""
    tenant_id = _tenant_id_from_user(user)
    await require_any_module_operational_write(tenant_id, ("support", "human_handoff"))
    if req.save_to_knowledge:
        await require_module_config_write(tenant_id, "faq")
    conversation = await support_service.get_conversation(tenant_id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.get("status") != "escalated":
        raise HTTPException(status_code=409, detail="Conversation is not currently escalated")
    actor_id = user.get("user_id")
    provider_message_id = f"tenant_reply_{uuid4()}"
    delivery_channel = conversation.get("channel_type") or "whatsapp"
    delivery_status = "sent"
    raw_payload = {
        "source": "tenant_escalation_reply",
        "actor_id": str(actor_id),
        "save_to_knowledge": req.save_to_knowledge,
    }
    if delivery_channel == "awa_chat":
        session = await support_service.get_public_chat_session_for_conversation(tenant_id, conversation_id)
        if _public_session_is_reachable(session):
            raw_payload["public_chat_session_id"] = str(session["id"])
        elif conversation.get("customer_email"):
            delivery_channel = "email"
            delivery_status = "email_queued"
            raw_payload.update({
                "fallback": "email",
                "email_to": conversation["customer_email"],
                "public_chat_session_status": session.get("status") if session else None,
            })
        else:
            delivery_status = "pending_customer_contact"
            raw_payload.update({
                "fallback": "awaiting_customer_contact",
                "public_chat_session_status": session.get("status") if session else None,
            })
        await _save_outbound_reply(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_text=req.message_text,
            channel_type=delivery_channel,
            provider_message_id=provider_message_id,
            raw_payload=raw_payload,
            delivery_status=delivery_status,
        )
    elif delivery_channel == "whatsapp":
        customer_phone = conversation.get("customer_phone")
        if not customer_phone or customer_phone == "Browser chat":
            raise HTTPException(status_code=409, detail="Customer phone is required for WhatsApp replies")
        account = None
        if conversation.get("channel_account_id"):
            account = await support_service.get_whatsapp_account_for_dispatch(
                tenant_id,
                conversation["channel_account_id"],
            )
        dispatch_result = await whatsapp_client.send_text_message(
            to_phone=customer_phone,
            message_text=req.message_text,
            phone_number_id=account.get("phone_number_id") if account else None,
            access_token=account.get("access_token") if account else None,
        )
        if dispatch_result.get("message_id"):
            provider_message_id = dispatch_result["message_id"]
        delivery_status = "sent" if dispatch_result.get("success") else "failed"
        raw_payload["dispatch_result"] = dispatch_result
        await _save_outbound_reply(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            message_text=req.message_text,
            channel_type="whatsapp",
            provider_message_id=provider_message_id,
            raw_payload=raw_payload,
            delivery_status=delivery_status,
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported conversation channel")
    knowledge_entry = None
    if req.save_to_knowledge:
        knowledge_entry = await _save_reply_to_knowledge(
            tenant_id=tenant_id,
            conversation=conversation,
            reply_text=req.message_text,
            actor_id=actor_id,
        )
    updated_conversation = conversation
    if req.close_after_reply:
        updated_conversation = await support_service.resolve_conversation(tenant_id, conversation_id)
        await _set_session_state(tenant_id, conversation_id, ConversationState.COMPLETED)
    return APIResponse.create_success(data={
        "conversation": updated_conversation,
        "message_id": provider_message_id,
        "delivery_channel": delivery_channel,
        "delivery_status": delivery_status,
        "knowledge_saved": knowledge_entry is not None,
        "knowledge_id": str(knowledge_entry["id"]) if knowledge_entry else None,
    })
@router.post("/conversations/{conversation_id}/escalation/return-to-awa")
async def return_escalation_to_awa(
    conversation_id: UUID,
    user: dict = Depends(require_tenant_operator)
):
    """Clear escalation and let AWA continue handling future customer messages."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    await require_any_module_operational_write(tenant_id, ("support", "human_handoff"))
    conversation = await support_service.return_to_awa(tenant_id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _set_session_state(tenant_id, conversation_id, ConversationState.INTENT_IDENTIFIED)
    return APIResponse.create_success(data=conversation)
@router.post("/conversations/{conversation_id}/escalation/resolve")
async def resolve_escalation(
    conversation_id: UUID,
    user: dict = Depends(require_tenant_operator)
):
    """Resolve an escalated conversation and close it for the tenant inbox."""
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    await require_any_module_operational_write(tenant_id, ("support", "human_handoff"))
    conversation = await support_service.resolve_conversation(tenant_id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await _set_session_state(tenant_id, conversation_id, ConversationState.COMPLETED)
    return APIResponse.create_success(data=conversation)
@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: UUID,
    cursor: Optional[str] = Query(None, description="Pagination cursor"),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(get_current_user)
):
    """List messages for a specific conversation."""
    decoded = decode_cursor(cursor) if cursor else None
    result = await db_client.list_messages(
        user["tenant_id"], conversation_id, cursor=decoded, limit=limit
    )
    return APIResponse.create_success(data=result["items"], meta={"next_cursor": result["next_cursor"]})
@router.patch("/conversations/{conversation_id}")
async def update_conversation_status(
    conversation_id: UUID,
    req: UpdateConversationRequest,
    user: dict = Depends(require_tenant_operator)
):
    """Update the status of a conversation (e.g. active, closed, escalated)."""
    if req.status not in CONVERSATION_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    tenant_id = UUID(user.get("tenant_id")) if isinstance(user.get("tenant_id"), str) else user.get("tenant_id")
    await require_any_module_operational_write(tenant_id, ("support", "human_handoff"))
    success = await support_service.update_conversation_status(tenant_id, conversation_id, req.status)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return APIResponse.create_success(data={"updated": True, "status": req.status})
