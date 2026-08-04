import pytest
from uuid import uuid4
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.modules.booking_module import booking_module
from backend.modules.order_module import order_module

def make_context(step_data, user_name="Asha", user_phone="+15550000000"):
    tenant_id = uuid4()
    config_data = {
        "services": [{"id": "cleaning", "name": "Cleaning"}],
        "catalog_items": [{"id": "pizza", "name": "Pizza", "price": 1000}],
        "modules": {
            "booking": {"enabled": True, "fields": [{"key": "customer_name", "type": "string"}]},
            "order": {"enabled": True, "fields": []}
        }
    }
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test Clinic", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number=user_phone, name=user_name),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="booking",
            state=SessionState(workflow_state=ConversationState.EXECUTING, step_data=step_data),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config_data),
        incoming_message={"text": "hi"},
    )

def test_booking_prefill_from_verified_user():
    step_data = {}
    context = make_context(step_data, user_name="Asha", user_phone="+15550000000")
    booking_module.prefill_from_message("", context)

    res = context.session_data.state.step_data
    assert res.get("phone") == "+15550000000"
    assert res.get("customer_name") == "Asha"

def test_booking_prefill_ignores_unknown():
    step_data = {}
    context = make_context(step_data, user_name="unknown", user_phone="unknown")
    booking_module.prefill_from_message("", context)

    res = context.session_data.state.step_data
    assert "phone" not in res
    assert "customer_name" not in res

def test_booking_prefill_does_not_overwrite_existing():
    step_data = {"phone": "+19999999999", "customer_name": "Original Name"}
    context = make_context(step_data, user_name="New Name", user_phone="+15550000000")
    booking_module.prefill_from_message("", context)

    res = context.session_data.state.step_data
    assert res["phone"] == "+19999999999"
    assert res["customer_name"] == "Original Name"

def test_order_prefill_from_verified_user():
    step_data = {}
    context = make_context(step_data, user_name="Asha", user_phone="+15550000000")
    order_module.prefill_from_message("", context)

    res = context.session_data.state.step_data
    assert res.get("phone") == "+15550000000"
    assert res.get("customer_name") == "Asha"
