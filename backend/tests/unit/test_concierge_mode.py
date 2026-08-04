import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from backend.core.context import ExecutionContext, Tenant, User, ConversationSession, TenantConfig
from backend.capabilities.gateway import CapabilityGateway
from backend.capabilities.models import CapabilityRequest
from backend.logic.engine.core import agentic_engine

@pytest.mark.anyio
async def test_concierge_mode_requires_confirmation():
    # Setup context in standard vs concierge mode
    tenant_id = uuid4()

    # 1. Standard mode - requires confirmation
    standard_tenant = Tenant(id=tenant_id, name="Test Biz", plan_code="pro", status="active", ai_mode="standard")
    standard_context = ExecutionContext(
        tenant=standard_tenant,
        user=User(id=uuid4(), phone_number="+919999999999"),
        session_data=ConversationSession(id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4()),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"modules": {"booking": {"enabled": True}}}),
        incoming_message={"text": "confirm", "channel_type": "awa_chat"}
    )

    request = CapabilityRequest.from_context("booking.execute", standard_context, {"service_id": "cleaning"})

    tool_def = MagicMock()
    tool_def.key = "booking.execute"
    tool_def.module_name = "booking"
    tool_def.module = MagicMock(spec=["get_tool_schema", "get_required_fields", "validate_input"])
    tool_def.module.get_tool_schema = MagicMock(return_value={"properties": {"service_id": {"type": "string"}}, "required": ["service_id"]})
    tool_def.module.get_required_fields = MagicMock(return_value=[])

    metadata = MagicMock()
    metadata.requires_confirmation = True
    metadata.requires_verified_customer = False
    metadata.supported_channels = ["awa_chat"]

    # Mock usage guard
    mock_guard = AsyncMock()
    mock_guard.ensure_tool_allowed = AsyncMock(return_value=None)

    gateway = CapabilityGateway(guard=mock_guard)

    # In standard mode, decision should be "needs_confirmation"
    decision = await gateway.evaluate(request, standard_context, tool_def, metadata)
    assert decision.status == "needs_confirmation"

    # 2. Concierge mode - confirmation is NOT bypassed (decision status should be "needs_confirmation")
    concierge_tenant = Tenant(id=tenant_id, name="Test Biz", plan_code="pro", status="active", ai_mode="concierge")
    concierge_context = ExecutionContext(
        tenant=concierge_tenant,
        user=User(id=uuid4(), phone_number="+919999999999"),
        session_data=ConversationSession(id=uuid4(), tenant_id=tenant_id, conversation_id=uuid4()),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"modules": {"booking": {"enabled": True}}}),
        incoming_message={"text": "confirm", "channel_type": "awa_chat"}
    )

    request_concierge = CapabilityRequest.from_context("booking.execute", concierge_context, {"service_id": "cleaning"})
    decision_concierge = await gateway.evaluate(request_concierge, concierge_context, tool_def, metadata)
    assert decision_concierge.status == "needs_confirmation"
