import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4
from backend.modules.lead_capture_module import lead_capture_module
from backend.modules.order_module import order_module
from backend.modules.support_module import support_module
from backend.core.context import ExecutionContext
from backend.pipeline.message_router import message_router
@pytest.mark.asyncio
async def test_lead_capture_validation():
    # Test valid and invalid emails
    valid = lead_capture_module.validate_input({"type": "email"}, "test@example.com")
    assert valid.is_valid is True
    invalid = lead_capture_module.validate_input({"type": "email"}, "bad-email")
    assert invalid.is_valid is False
    # Test phone
    valid_phone = lead_capture_module.validate_input({"type": "phone"}, "+1234567890")
    assert valid_phone.is_valid is True
@pytest.mark.asyncio
async def test_support_validation():
    # Urgency must be low/medium/high
    valid = support_module.validate_input({"type": "string", "key": "urgency"}, "high")
    assert valid.is_valid is True
    invalid = support_module.validate_input({"type": "string", "key": "urgency"}, "critical")
    assert invalid.is_valid is False
@pytest.mark.asyncio
async def test_order_validation_and_catalog_resolution():
    valid_quantity = order_module.validate_input({"type": "number", "key": "quantity"}, "2")
    assert valid_quantity.is_valid is True
    invalid_quantity = order_module.validate_input({"type": "number", "key": "quantity"}, "0")
    assert invalid_quantity.is_valid is False
    config = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "id": "food",
                        "name": "Food",
                        "items": [{"id": "veg_biriyani", "name": "Veg Biriyani", "price": 120, "enabled": True}],
                    }
                ],
            }
        }
    }
    item = order_module.resolve_item("2 veg biriyani delivery", config)
    assert item["id"] == "veg_biriyani"
def test_message_router():
    config = {
        "modules": {
            "order": {"enabled": True},
            "lead_capture": {"enabled": True},
            "support": {"enabled": True}
        }
    }
    assert message_router.classify_intent("I want to order biriyani", config) == "order"
    assert message_router.classify_intent("I need help with my account", config) == "support"
    assert message_router.classify_intent("Sign me up for the newsletter", config) == "lead_capture"
    assert message_router.classify_intent("Can I book a slot?", config) == "booking"
    # Check routing mapped to module names
    assert message_router.get_module_for_intent("order", config) == "order"
    assert message_router.get_module_for_intent("support", config) == "support"


def test_message_router_structured_decision_for_active_tangent():
    config = {
        "modules": {
            "booking": {"enabled": True},
            "order": {"enabled": True},
            "support": {"enabled": True},
        }
    }
    decision = message_router.route_message(
        "how much is it?",
        config,
        current_task="booking",
        current_module="booking",
    )
    assert decision.intent == "faq"
    assert decision.confidence >= 0.8
    assert decision.route_type == "direct_answer"
    assert decision.is_tangent is True
    assert decision.should_clear_active_task is False
    assert decision.reason == "active_task_faq_tangent"
