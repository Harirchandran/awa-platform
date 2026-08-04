import json
from types import SimpleNamespace
from uuid import uuid4

from backend.core.context import SessionState
from backend.logic.engine.capability_menu import (
    build_capability_menu,
    build_catalog_choices_response,
    build_direct_action_response,
)
from backend.logic.engine.core import WorkflowResult
from backend.pipeline.channel_output import SAFE_RESPONSE_FALLBACK, normalize_channel_payload, normalize_channel_response


def _button_titles(payload: dict) -> list[str]:
    return [
        button["reply"]["title"]
        for button in payload["interactive_data"]["action"]["buttons"]
    ]


def _interactive_titles(payload: dict) -> list[str]:
    action = payload["interactive_data"]["action"]
    if "buttons" in action:
        return [button["reply"]["title"] for button in action["buttons"]]
    return [row["title"] for section in action["sections"] for row in section["rows"]]


def _catalog_config() -> dict:
    return {
        "business_name": "Smile Clinic",
        "services": [
            {"id": "cleaning", "name": "Dental Cleaning", "duration_mins": 45, "price": 800},
            {"id": "checkup", "name": "General Checkup", "duration_mins": 30, "price": 500},
        ],
        "modules": {
            "booking": {"enabled": True, "fields": []},
            "order": {
                "enabled": True,
                "fields": [],
                "categories": [
                    {
                        "id": "food",
                        "name": "Food",
                        "items": [{"id": "tea", "name": "Masala Tea", "price": 30, "enabled": True}],
                    }
                ],
            },
            "faq": {"enabled": True, "fields": []},
        },
    }


def test_workflow_result_accepts_valid_json_envelope():
    result = WorkflowResult(
        response_text=json.dumps({"response_text": "Please choose.", "quick_replies": ["Confirm", "Change"]})
    )

    assert result.response_text == "Please choose."
    assert result.quick_replies == ["Confirm", "Change"]


def test_malformed_json_envelope_falls_back_to_safe_plain_text():
    raw = '{"response_text":"Please choose","quick_replies":["Confirm",]}'

    result = WorkflowResult(response_text=raw)
    normalized = normalize_channel_response(raw, "awa_chat")

    assert result.response_text == SAFE_RESPONSE_FALLBACK
    assert normalized == SAFE_RESPONSE_FALLBACK
    assert "response_text" not in normalized


def test_legacy_bracket_parsing_still_works_as_fallback():
    payload = normalize_channel_payload("Please choose. [Confirm] [Change]", "awa_chat")

    assert payload["text"] == "Please choose."
    assert _button_titles(payload) == ["Confirm", "Change"]


def test_deterministic_menu_and_direct_responses_use_structured_quick_replies():
    config = _catalog_config()
    menu = build_capability_menu(config, business_name="Smile Clinic")
    catalog = build_catalog_choices_response(config, ordering_mode=False)
    context = SimpleNamespace(
        config=SimpleNamespace(config_data=config),
        session_data=SimpleNamespace(state=SessionState(), current_module=None),
    )
    direct = build_direct_action_response(context, "Book appointment")

    assert "[" not in menu.text
    assert menu.suggestions == ["Show menu", "Place simple order", "Book appointment", "Services & prices", "Opening hours"]
    assert "[" not in catalog.text
    assert catalog.quick_replies == ["Masala Tea", "Place simple order"]
    assert direct is not None
    assert "[" not in direct.text
    assert direct.quick_replies == ["Dental Cleaning", "General Checkup"]


def test_explicit_quick_replies_override_bracket_derived_replies():
    payload = normalize_channel_payload(
        "Please choose. [Old] [Cancel]",
        "awa_chat",
        quick_replies=["New", "Cancel"],
    )

    assert payload["text"] == "Please choose."
    assert _button_titles(payload) == ["New", "Cancel"]


def test_explicit_quick_replies_are_sanitized_deduped_bounded_and_uuid_free():
    internal_id = str(uuid4())
    payload = normalize_channel_payload(
        "Pick one.",
        "awa_chat",
        quick_replies=[
            f"Track {internal_id}",
            "Confirm",
            "Confirm",
            "This label is much too long for a WhatsApp button",
            "Another very long label for truncation",
            "One more long label for truncation",
        ],
    )
    titles = _interactive_titles(payload)

    assert len(titles) == 5
    assert all(len(title) <= 24 for title in titles)
    assert all(internal_id not in title for title in titles)
    assert len({title.lower() for title in titles}) == len(titles)


def test_quick_replies_reject_hidden_action_metadata_tool_syntax_and_success_claims():
    payload = normalize_channel_payload(
        "Pick one.",
        "awa_chat",
        quick_replies=[
            "Book appointment",
            "action_id=select_booking_slot",
            "booking.execute",
            "booking_execute",
            "tool_call create_booking",
            "Payment completed",
            "Book appointment!",
            "Cambiar horario",
        ],
    )
    titles = _interactive_titles(payload)

    assert titles == ["Book appointment", "Cambiar horario"]
    assert all("id" not in title.lower() for title in titles)
    assert all("tool" not in title.lower() for title in titles)


def test_legacy_bracket_quick_replies_use_same_semantic_only_sanitizer():
    payload = normalize_channel_payload(
        "Please choose. [Confirm] [booking.execute] [action:cancel] [Change]",
        "awa_chat",
    )

    assert payload["text"] == "Please choose."
    assert _button_titles(payload) == ["Confirm", "Change"]


def test_structured_quick_replies_are_not_reused_across_turns():
    first = normalize_channel_payload("First.", "awa_chat", quick_replies=["Confirm", "Change"])
    second = normalize_channel_payload("Second turn has no options.", "awa_chat")

    assert "interactive_data" in first
    assert second == {"text": "Second turn has no options.", "cards": []}


def test_awa_chat_payload_shape_matches_existing_interactive_contract():
    payload = normalize_channel_payload("Choose.", "awa_chat", quick_replies=["Confirm", "Change"])

    assert payload["text"] == "Choose."
    assert payload["cards"] == []
    assert payload["interactive_data"]["type"] == "button"
    assert _button_titles(payload) == ["Confirm", "Change"]


def test_whatsapp_explicit_quick_replies_build_compatible_interactive_payload():
    payload = normalize_channel_payload("Choose.", "whatsapp", quick_replies=["One", "Two", "Three", "Four"])

    assert payload["text"] == "Choose."
    assert payload["interactive_data"]["type"] == "list"
    rows = payload["interactive_data"]["action"]["sections"][0]["rows"]
    assert [row["title"] for row in rows] == ["One", "Two", "Three", "Four"]


def test_valid_raw_json_envelope_never_leaks_to_customer_visible_output():
    raw = '{"response_text":"Choose now.","quick_replies":["Confirm","Change"]}'
    payload = normalize_channel_payload(raw, "awa_chat")

    assert payload["text"] == "Choose now."
    assert "response_text" not in payload["text"]
    assert _button_titles(payload) == ["Confirm", "Change"]
