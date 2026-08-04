from __future__ import annotations

import pytest

from backend.core.runtime_behavior_profile import RuntimeBehaviorProfileResolution
from backend.logic.engine.capability_menu import build_capability_menu, is_explicit_capability_request
from backend.schemas.behavior_profile import EffectiveBehaviorProfile


def _config() -> dict:
    return {
        "business_name": "Transcript Business",
        "modules": {
            "booking": {"enabled": True},
            "order": {
                "enabled": True,
                "categories": [{"name": "Menu", "items": [{"name": "Available item", "enabled": True}]}],
            },
            "faq": {"enabled": True},
            "support": {"enabled": True},
            "human_handoff": {"enabled": True},
        },
    }


def _profile(
    *,
    journey_priority: tuple[str, ...] = ("booking", "order", "faq", "support"),
    greeting_mode: str = "hybrid",
    menu_policy: str = "show_available_workflows",
    handoff_policy: str = "when_stuck",
) -> RuntimeBehaviorProfileResolution:
    return RuntimeBehaviorProfileResolution(
        source="active",
        effective_profile=EffectiveBehaviorProfile(
            schema_version=1,
            profile_name="Transcript Profile",
            journey_priority=journey_priority,
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


@pytest.mark.parametrize(
    ("scenario", "available_modules", "profile", "expected_first", "expected_phrase"),
    [
        ("new_customer", {"booking", "order", "faq"}, _profile(), "Book appointment", "What would you like to do?"),
        ("menu_first", {"booking", "order", "faq"}, _profile(greeting_mode="menu_first"), "Book appointment", "Choose an option"),
        ("ask_need_first", {"booking", "order", "faq"}, _profile(greeting_mode="ask_need_first"), None, "What can I help you with today?"),
        ("hybrid", {"booking", "order", "faq"}, _profile(greeting_mode="hybrid"), "Book appointment", "What would you like to do?"),
        ("booking_first", {"booking", "order", "faq"}, _profile(journey_priority=("booking", "order", "faq")), "Book appointment", "Hi,"),
        ("order_first", {"booking", "order", "faq"}, _profile(journey_priority=("order", "booking", "faq")), "Show menu", "Hi,"),
        ("faq_first", {"faq"}, _profile(journey_priority=("faq", "booking", "order")), "Services & prices", "Hi,"),
        ("support_first", {"support"}, _profile(journey_priority=("support", "booking", "order")), "Get support", "Hi,"),
    ],
)
def test_public_chat_greeting_transcripts_follow_only_runtime_available_profile_preferences(
    scenario,
    available_modules,
    profile,
    expected_first,
    expected_phrase,
):
    menu = build_capability_menu(
        _config(),
        business_name="Transcript Business",
        available_modules=available_modules,
        behavior_profile_resolution=profile,
    )

    assert menu.text.startswith("Hi,")
    assert expected_phrase in menu.text
    if expected_first is None:
        assert menu.suggestions == []
    else:
        assert menu.suggestions[0] == expected_first
    assert all(isinstance(reply, str) for reply in menu.suggestions)
    if "booking" not in available_modules:
        assert "Book appointment" not in menu.suggestions
    if "order" not in available_modules:
        assert "Show menu" not in menu.suggestions
        assert "Place simple order" not in menu.suggestions
    if "support" not in available_modules and "human_handoff" not in available_modules:
        assert "Get support" not in menu.suggestions
        assert "Contact support" not in menu.suggestions


def test_hide_menu_unless_asked_transcript_suppresses_initial_menu_but_honors_explicit_request():
    profile = _profile(menu_policy="hide_menu_unless_asked")
    available = {"booking", "order", "faq"}

    greeting = build_capability_menu(
        _config(),
        business_name="Transcript Business",
        available_modules=available,
        behavior_profile_resolution=profile,
    )
    explicit_capability_request = build_capability_menu(
        _config(),
        business_name="Transcript Business",
        available_modules=available,
        behavior_profile_resolution=profile,
        menu_requested=is_explicit_capability_request("What can you help me with?"),
    )

    assert greeting.suggestions == []
    assert "What can I help you with today?" in greeting.text
    assert explicit_capability_request.suggestions[:3] == ["Book appointment", "Show menu", "Place simple order"]
    assert "currently available" in explicit_capability_request.text.lower()
    assert is_explicit_capability_request("hello") is False


def test_public_chat_transcript_never_exposes_profile_priorities_that_runtime_filters_out():
    tenant_disabled = _config()
    tenant_disabled["modules"]["booking"]["enabled"] = False
    profile = _profile(journey_priority=("booking", "support", "order", "faq"), handoff_policy="early_offer")

    disabled_booking = build_capability_menu(
        tenant_disabled,
        available_modules={"booking", "order", "faq"},
        behavior_profile_resolution=profile,
    )
    plan_disallowed_order = build_capability_menu(
        _config(),
        available_modules={"booking", "faq"},
        behavior_profile_resolution=_profile(journey_priority=("order", "booking", "faq")),
    )
    unavailable_handoff = build_capability_menu(
        _config(),
        available_modules={"faq"},
        behavior_profile_resolution=profile,
    )

    assert "Book appointment" not in disabled_booking.suggestions
    assert disabled_booking.suggestions[:2] == ["Show menu", "Place simple order"]
    assert "Show menu" not in plan_disallowed_order.suggestions
    assert "Place simple order" not in plan_disallowed_order.suggestions
    assert "Book appointment" in plan_disallowed_order.suggestions
    assert "Contact support" not in unavailable_handoff.suggestions
    assert "Get support" not in unavailable_handoff.suggestions


def test_public_chat_no_capabilities_transcript_keeps_generic_help_without_workflow_actions():
    config = _config()
    for module in config["modules"].values():
        module["enabled"] = False

    menu = build_capability_menu(
        config,
        available_modules=set(),
        behavior_profile_resolution=_profile(journey_priority=("booking", "order", "support")),
    )

    assert menu.capabilities == ["business questions"]
    assert "Book appointment" not in menu.suggestions
    assert "Show menu" not in menu.suggestions
    assert "Place simple order" not in menu.suggestions
    assert all(isinstance(reply, str) for reply in menu.suggestions)
