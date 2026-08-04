import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from backend.core.config import settings
from backend.core.context import ExecutionContext, Tenant, User, ConversationSession, TenantConfig, SessionState
from backend.core.states import ConversationState
from backend.capabilities.gateway import CapabilityGateway
from backend.capabilities.models import CapabilityRequest
from backend.domains.payments.tool_definitions import get_payments_tool_schema
from backend.domains.payments.tool_handlers import execute_payments_tool
from backend.tools.registry import ToolExecutionResult
from backend.logic.engine.field_extraction import extract_payment_fields

pytestmark = pytest.mark.anyio

@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)

def _make_context(ai_mode="concierge", step_data=None):
    tenant_id = uuid4()
    session_state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data=dict(step_data or {}),
    )
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test Biz", plan_code="pro", status="active", ai_mode=ai_mode),
        user=User(id=uuid4(), phone_number="+919999999999"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="payments",
            state=session_state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Test Biz",
                "modules": {
                    "payments": {"enabled": True, "provider": "razorpay", "use_platform_razorpay": True},
                    "order": {"enabled": True},
                    "booking": {"enabled": True},
                    "reminders": {"enabled": True},
                    "reviews": {"enabled": True},
                },
            },
        ),
        incoming_message={"text": "confirm", "channel_type": "awa_chat"}
    )

# A. Concierge confirmation boundary
async def test_concierge_mode_protected_actions_require_confirmation():
    mock_guard = AsyncMock()
    mock_guard.ensure_tool_allowed = AsyncMock(return_value=None)
    gateway = CapabilityGateway(guard=mock_guard)

    for tool_key in ["payments.execute", "order.create", "booking.create", "reminders.create", "reviews.create"]:
        context = _make_context(ai_mode="concierge")
        request = CapabilityRequest.from_context(tool_key, context, {"amount": 100})

        tool_def = MagicMock()
        tool_def.key = tool_key
        tool_def.module_name = tool_key.split(".")[0]
        tool_def.module = MagicMock(spec=["get_tool_schema", "get_required_fields", "validate_input"])
        tool_def.module.get_tool_schema = MagicMock(return_value={"properties": {"amount": {"type": "integer"}}, "required": []})
        tool_def.module.get_required_fields = MagicMock(return_value=[])

        metadata = MagicMock()
        metadata.requires_confirmation = True
        metadata.requires_verified_customer = False
        metadata.supported_channels = ["awa_chat"]

        decision = await gateway.evaluate(request, context, tool_def, metadata)
        assert decision.status == "needs_confirmation", f"Tool {tool_key} bypassed confirmation in concierge mode"

# B. Payment schema hardening
def test_payment_schema_excludes_unsafe_fields():
    schema = get_payments_tool_schema({"modules": {"payments": {"enabled": True}}})
    properties = schema.get("properties", {})

    forbidden = [
        "provider", "status", "payment_status", "paid", "success", "captured", "failed", "refunded",
        "payment_link_url", "provider_payment_id", "provider_order_id", "transaction_id",
        "signature", "webhook_payload", "refund_id", "settlement_id", "order_id", "booking_id", "invoice_id",
        "tenant_id", "user_id", "customer_id", "payment_id", "payment_type", "type"
    ]

    for field in forbidden:
        assert field not in properties, f"Payment schema exposed forbidden field: {field}"

# B. Payment schema tool call rejection
async def test_payment_tool_call_rejects_unsafe_fields():
    logger = MagicMock()
    payment_service = AsyncMock()
    create_link = AsyncMock()

    forbidden = [
        "provider", "status", "payment_status", "paid", "success", "captured", "failed", "refunded",
        "payment_link_url", "provider_payment_id", "provider_order_id", "transaction_id",
        "signature", "webhook_payload", "refund_id", "settlement_id", "order_id", "booking_id", "invoice_id",
        "tenant_id", "user_id", "customer_id", "payment_id", "payment_type", "type"
    ]

    for field in forbidden:
        context = _make_context(step_data={"amount": 100, field: "someval"})
        result = await execute_payments_tool(context, payment_service, create_link, logger)
        assert result.success is False, f"Payment execution did not fail-closed for field: {field}"
        assert field in result.validation_errors

# C. Provider authority from trusted config
async def test_payment_provider_authority_from_config():
    logger = MagicMock()
    payment_service = AsyncMock()
    create_link = AsyncMock()

    context = _make_context(step_data={"amount": 100})
    context.config.config_data["modules"]["payments"]["provider"] = "manual"

    payment_id = uuid4()
    payment_service.create_payment_request.return_value = {
        "id": payment_id,
        "status": "draft",
        "provider": "manual",
        "payment_url": None,
    }

    result = await execute_payments_tool(context, payment_service, create_link, logger)
    assert result.success is True
    assert result.data["provider"] == "manual"

# D. Semantic extraction blocklist rejection
async def test_semantic_payment_extraction_rejects_provider_fail_closed(monkeypatch):
    from langchain_core.messages import AIMessage
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(
            content='{"detected_language": "English", "workflow": "payment", "workflow_action": "continue", "confidence": 0.9, "field_updates": {"amount": "100", "provider": "razorpay"}, "field_confidence": {"amount": 0.9, "provider": 0.9}, "clarification_needed": false, "clarification_fields": []}'
        )
    )
    monkeypatch.setattr("backend.logic.engine.llm.get_llm", lambda: mock_llm)

    context = _make_context()
    extraction = await extract_payment_fields(context)
    assert extraction.confidence == 0.0
    assert extraction.workflow == "unknown"
    assert extraction.field_updates == {}

async def test_semantic_payment_extraction_rejects_payment_type_fail_closed(monkeypatch):
    from langchain_core.messages import AIMessage
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(
            content='{"detected_language": "English", "workflow": "payment", "workflow_action": "continue", "confidence": 0.9, "field_updates": {"amount": "100", "payment_type": "booking_deposit"}, "field_confidence": {"amount": 0.9, "payment_type": 0.9}, "clarification_needed": false, "clarification_fields": []}'
        )
    )
    monkeypatch.setattr("backend.logic.engine.llm.get_llm", lambda: mock_llm)

    context = _make_context()
    extraction = await extract_payment_fields(context)
    assert extraction.confidence == 0.0
    assert extraction.workflow == "unknown"
    assert extraction.field_updates == {}

# E. Invalid/missing backend provider config
async def test_invalid_backend_provider_config_fails_honestly():
    logger = MagicMock()
    payment_service = AsyncMock()
    create_link = AsyncMock()

    context = _make_context(step_data={"amount": 100})
    context.config.config_data["modules"]["payments"]["provider"] = "invalid_prov"

    result = await execute_payments_tool(context, payment_service, create_link, logger)
    assert result.success is False
    assert result.validation_errors["provider"] == "Unsupported customer payment provider."
