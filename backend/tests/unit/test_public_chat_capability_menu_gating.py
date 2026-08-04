from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from backend.api.routes import public_chat
from backend.core.runtime_capabilities import RuntimeCapability, RuntimeCapabilitySnapshot
from backend.core.runtime_behavior_profile import RuntimeBehaviorProfileResolution
from backend.logic.engine.capability_menu import build_capability_menu
from backend.schemas.behavior_profile import EffectiveBehaviorProfile


def _config():
    return {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [{"name": "Menu", "items": [{"name": "Item", "enabled": True}]}],
            },
            "booking": {"enabled": True},
            "payments": {"enabled": True},
            "lead_capture": {"enabled": True},
            "support": {"enabled": True},
            "reminders": {"enabled": True},
            "reviews": {"enabled": True},
            "faq": {"enabled": True},
        }
    }


def _snapshot(tenant_id, available):
    all_modules = {"order", "booking", "payments", "lead_capture", "support", "reminders", "reviews", "faq", "human_handoff"}
    return RuntimeCapabilitySnapshot(
        tenant_id=tenant_id,
        modules={
            module: RuntimeCapability(
                module=module,
                enabled_by_tenant=True,
                allowed_by_plan=module in available,
                runtime_available=module in available,
                reason="available" if module in available else "plan_disallowed",
            )
            for module in all_modules
        },
    )


def _profile(
    *,
    journey_priority=("booking", "order", "support", "faq"),
    greeting_mode="hybrid",
    menu_policy="show_available_workflows",
    handoff_policy="when_stuck",
):
    return RuntimeBehaviorProfileResolution(
        source="active",
        effective_profile=EffectiveBehaviorProfile(
            schema_version=1,
            profile_name="Public Chat Profile",
            business_type_key="service_business",
            journey_priority=tuple(journey_priority),
            greeting_mode=greeting_mode,
            clarification_style="compact",
            tone="friendly",
            language_policy="follow_customer",
            price_visibility="show_when_available",
            service_detail_policy="brief",
            handoff_policy=handoff_policy,
            fallback_policy="clarify_first",
            question_batching="single",
            menu_policy=menu_policy,
        ),
    )


class BrokenProfileResolution:
    source = "active"

    @property
    def effective_profile(self):
        raise ValueError("raw unsafe profile")


def test_menu_hides_plan_disallowed_order_and_booking_workflows():
    menu = build_capability_menu(_config(), available_modules={"faq"})

    assert "simple orders" not in menu.capabilities
    assert "appointments" not in menu.capabilities
    assert "Show menu" not in menu.suggestions
    assert "Place simple order" not in menu.suggestions
    assert "Book appointment" not in menu.suggestions
    assert all(isinstance(reply, str) for reply in menu.suggestions)


def test_menu_shows_order_only_when_runtime_available_and_catalog_exists():
    menu = build_capability_menu(_config(), available_modules={"order", "faq"})

    assert "simple orders" in menu.capabilities
    assert "Show menu" in menu.suggestions
    assert "Place simple order" in menu.suggestions

    empty = _config()
    empty["modules"]["order"]["categories"] = []
    no_catalog_menu = build_capability_menu(empty, available_modules={"order", "faq"})
    assert "simple orders" not in no_catalog_menu.capabilities
    assert "Show menu" not in no_catalog_menu.suggestions


def test_menu_first_greeting_uses_filtered_profile_priority():
    menu = build_capability_menu(
        _config(),
        business_name="Tenant",
        available_modules={"booking", "order", "faq"},
        behavior_profile_resolution=_profile(journey_priority=("booking", "order", "payments"), greeting_mode="menu_first"),
    )

    assert menu.suggestions[:3] == ["Book appointment", "Show menu", "Place simple order"]
    assert "Payments" not in " ".join(menu.suggestions)
    assert "Choose an option" in menu.text


def test_ask_need_first_suppresses_forced_menu_and_asks_open_question():
    menu = build_capability_menu(
        _config(),
        business_name="Tenant",
        available_modules={"booking", "order", "faq"},
        behavior_profile_resolution=_profile(greeting_mode="ask_need_first"),
    )

    assert menu.suggestions == []
    assert "What can I help you with today?" in menu.text


def test_hybrid_greeting_shows_open_question_plus_available_suggestions():
    menu = build_capability_menu(
        _config(),
        business_name="Tenant",
        available_modules={"booking", "order", "faq"},
        behavior_profile_resolution=_profile(journey_priority=("booking", "order"), greeting_mode="hybrid"),
    )

    assert "What would you like to do?" in menu.text
    assert menu.suggestions[:3] == ["Book appointment", "Show menu", "Place simple order"]


def test_hide_menu_unless_asked_suppresses_initial_workflow_menu():
    menu = build_capability_menu(
        _config(),
        business_name="Tenant",
        available_modules={"booking", "order", "faq"},
        behavior_profile_resolution=_profile(menu_policy="hide_menu_unless_asked"),
    )

    assert menu.suggestions == []
    assert "What can I help you with today?" in menu.text


def test_profile_priority_cannot_show_unavailable_modules_or_handoff():
    config = _config()
    config["modules"]["booking"]["enabled"] = False
    menu = build_capability_menu(
        config,
        business_name="Tenant",
        available_modules={"order", "faq"},
        behavior_profile_resolution=_profile(journey_priority=("booking", "support", "order")),
    )

    assert "Book appointment" not in menu.suggestions
    assert "Contact support" not in menu.suggestions
    assert menu.suggestions[:2] == ["Show menu", "Place simple order"]

    plan_disallowed = build_capability_menu(
        _config(),
        business_name="Tenant",
        available_modules={"booking", "faq"},
        behavior_profile_resolution=_profile(journey_priority=("order", "booking")),
    )
    assert "Show menu" not in plan_disallowed.suggestions
    assert "Place simple order" not in plan_disallowed.suggestions
    assert "Book appointment" in plan_disallowed.suggestions


def test_services_can_be_informational_without_implying_booking():
    config = _config()
    config["services"] = [{"name": "Consultation", "price": 100}]
    config["modules"]["booking"]["enabled"] = False

    menu = build_capability_menu(
        config,
        business_name="Tenant",
        available_modules={"faq"},
        behavior_profile_resolution=_profile(journey_priority=("booking", "faq")),
    )

    assert "Services & prices" in menu.suggestions
    assert "Book appointment" not in menu.suggestions
    assert "appointments" not in menu.capabilities


def test_missing_or_malformed_profile_falls_back_to_legacy_greeting():
    legacy = build_capability_menu(_config(), business_name="Tenant", available_modules={"booking", "order", "faq"})
    fallback = build_capability_menu(
        _config(),
        business_name="Tenant",
        available_modules={"booking", "order", "faq"},
        behavior_profile_resolution=RuntimeBehaviorProfileResolution(
            source="legacy_fallback",
            effective_profile=_profile().effective_profile,
            warning_code="no_active_behavior_preset",
        ),
    )
    malformed = build_capability_menu(
        _config(),
        business_name="Tenant",
        available_modules={"booking", "order", "faq"},
        behavior_profile_resolution=BrokenProfileResolution(),
    )

    assert fallback == legacy
    assert malformed == legacy
    assert legacy.text.strip()


@pytest.mark.asyncio
async def test_public_link_skips_catalog_hydration_when_order_is_plan_disallowed():
    tenant_id = uuid4()
    link = {
        "id": uuid4(), "slug": "safe-link", "tenant_id": tenant_id,
        "tenant_name": "Tenant", "display_name": "Tenant", "status": "active",
        "tenant_status": "active", "plan_code": "custom-restricted",
    }
    snapshot = _snapshot(tenant_id, {"booking", "faq"})

    with (
        patch.object(public_chat, "_check_public_rate_limit", new=AsyncMock()),
        patch.object(public_chat, "_get_active_link", new=AsyncMock(return_value=link)),
        patch.object(public_chat, "_get_public_config", new=AsyncMock(return_value=_config())),
        patch.object(public_chat.db_client, "record_public_chat_link_open", new=AsyncMock()),
        patch("backend.core.runtime_capabilities.build_runtime_capability_snapshot", new=AsyncMock(return_value=snapshot)),
        patch("backend.logic.engine.capability_menu.hydrate_public_catalog", new=AsyncMock()) as hydrate,
    ):
        response = await public_chat.get_public_chat_link("safe-link", MagicMock())

    payload = response
    replies = payload["data"]["suggested_replies"]
    hydrate.assert_not_awaited()
    assert "Show menu" not in replies
    assert "Place simple order" not in replies
    assert "Book appointment" in replies
    assert "plan_disallowed" not in str(payload)
