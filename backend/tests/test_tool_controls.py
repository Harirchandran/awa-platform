import pytest
from backend.api.routes.public_chat import _build_privacy_note, _build_public_chat_suggestions
from backend.services.tool_controls import apply_tool_control_update, build_tool_control_state
def test_tool_control_state_marks_plan_locked_tools():
    state = build_tool_control_state(
        {
            "business_name": "Glow Salon",
            "business_type": "salon",
            "modules": {
                "booking": {"enabled": True, "fields": []},
                "order": {"enabled": True, "fields": []},
            },
        },
        allowed_modules=["booking", "faq", "human_handoff"],
    )
    order = next(tool for tool in state["tools"] if tool["key"] == "order")
    booking = next(tool for tool in state["tools"] if tool["key"] == "booking")
    assert booking["enabled"] is True
    assert booking["plan_allowed"] is True
    assert order["enabled"] is True
    assert order["plan_allowed"] is False
    assert order["locked_reason"] == "Not available on this tenant plan"
def test_apply_tool_control_update_preserves_module_details():
    updated = apply_tool_control_update(
        {
            "business_name": "Glow Salon",
            "business_type": "salon",
            "modules": {
                "booking": {
                    "enabled": True,
                    "fields": [{"key": "customer_name", "label": "Name", "type": "string", "required": True}],
                },
                "order": {
                    "enabled": True,
                    "fields": [],
                    "categories": [{"id": "haircare", "name": "Haircare", "items": []}],
                },
            },
        },
        enabled_modules=["booking", "faq", "human_handoff"],
        allowed_modules=["booking", "order", "faq", "human_handoff"],
        business_type="salon",
    )
    assert updated["business_type"] == "salon"
    assert updated["modules"]["booking"]["enabled"] is True
    assert updated["modules"]["booking"]["fields"][0]["key"] == "customer_name"
    assert updated["modules"]["order"]["enabled"] is False
    assert updated["modules"]["order"]["categories"][0]["name"] == "Haircare"
    assert updated["modules"]["faq"]["enabled"] is True
def test_apply_tool_control_update_blocks_plan_restricted_tools():
    with pytest.raises(PermissionError):
        apply_tool_control_update(
            {"business_name": "Glow Salon", "modules": {}},
            enabled_modules=["order"],
            allowed_modules=["booking", "faq", "human_handoff"],
        )
def test_tool_control_state_uses_database_tool_definitions():
    tool_catalog = [
        {
            "key": "loyalty",
            "label": "Loyalty",
            "category": "growth",
            "description": "Track rewards.",
            "icon_key": "star",
            "route_path": "/dashboard/loyalty",
            "actions": [],
            "sort_order": 10,
        }
    ]
    state = build_tool_control_state(
        {
            "business_name": "Rewards Shop",
            "modules": {"loyalty": {"enabled": True, "fields": []}},
        },
        allowed_modules=["loyalty"],
        tool_definitions=tool_catalog,
    )
    assert [tool["key"] for tool in state["tools"]] == ["loyalty"]
    assert state["tools"][0]["enabled"] is True
    assert state["tools"][0]["route_path"] == "/dashboard/loyalty"
def test_apply_tool_control_update_accepts_database_defined_tool():
    updated = apply_tool_control_update(
        {"business_name": "Rewards Shop", "modules": {}},
        enabled_modules=["loyalty"],
        allowed_modules=["loyalty"],
        tool_definitions=[{
            "key": "loyalty",
            "label": "Loyalty",
            "category": "growth",
            "description": "Track rewards.",
        }],
    )
    assert updated["modules"]["loyalty"]["enabled"] is True
def test_public_chat_suggestions_follow_enabled_modules():
    salon_suggestions = _build_public_chat_suggestions({
        "modules": {
            "booking": {"enabled": True},
            "order": {"enabled": False},
            "faq": {"enabled": True},
            "human_handoff": {"enabled": True},
        }
    })
    restaurant_suggestions = _build_public_chat_suggestions({
        "modules": {
            "booking": {"enabled": False},
            "order": {
                "enabled": True,
                "categories": [
                    {"name": "Food", "items": [{"name": "Veg Biriyani", "enabled": True}]}
                ],
            },
            "faq": {"enabled": True},
            "human_handoff": {"enabled": True},
        }
    })
    assert "Book appointment" in salon_suggestions
    assert "Talk to a person" not in salon_suggestions
    assert "Place simple order" not in salon_suggestions
    assert "Place simple order" in restaurant_suggestions
    assert "Book appointment" not in restaurant_suggestions
def test_public_chat_order_suggestions_require_catalog_items():
    suggestions = _build_public_chat_suggestions({
        "modules": {
            "order": {"enabled": True, "categories": []},
            "support": {"enabled": True},
            "human_handoff": {"enabled": True},
        }
    })
    assert "Show menu" not in suggestions
    assert "Place simple order" not in suggestions
    assert "Get support" in suggestions
    assert "Talk to a person" not in suggestions
def test_public_chat_privacy_note_uses_enabled_workflows():
    note = _build_privacy_note({
        "modules": {
            "booking": {"enabled": True},
            "order": {"enabled": True},
        }
    })
    assert "booking or orders" in note
