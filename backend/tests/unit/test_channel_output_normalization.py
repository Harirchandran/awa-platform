import json
from backend.pipeline.channel_output import WHATSAPP_TEXT_LIMIT, normalize_channel_response
from backend.pipeline.interactive_parser import parse_interactive_hints
def test_channel_output_uses_user_message_from_tool_json():
    raw = json.dumps(
        {
            "success": False,
            "tool_key": "payments.execute",
            "denied_reason": "provider_secret_missing",
            "user_message": "Payment links are not configured yet. A team member can follow up.",
            "validation_errors": {"provider": "secret missing"},
        }
    )
    normalized = normalize_channel_response(raw, "awa_chat")
    assert normalized == "Payment links are not configured yet. A team member can follow up."
    assert "tool_key" not in normalized
    assert "provider_secret_missing" not in normalized
def test_whatsapp_output_is_length_bounded():
    normalized = normalize_channel_response("A" * (WHATSAPP_TEXT_LIMIT + 1000), "whatsapp")
    assert len(normalized) <= WHATSAPP_TEXT_LIMIT
    assert "[message shortened]" in normalized
def test_awa_chat_quick_replies_survive_normalization():
    normalized = normalize_channel_response("Please choose. [Confirm] [Change] [Cancel]", "awa_chat")
    clean, interactive = parse_interactive_hints(normalized)
    assert clean == "Please choose."
    assert interactive is not None
    assert [button["reply"]["title"] for button in interactive["action"]["buttons"]] == [
        "Confirm",
        "Change",
        "Cancel",
    ]
def test_channel_output_sanitization_leaks():
    from backend.pipeline.channel_output import is_high_risk_content, sanitize_customer_visible_output
    # 1. Test parenthetical leaks
    leak1 = "Executing now: (function=booking_execute)({\"patient_name\": \"hari\"})"
    assert is_high_risk_content(leak1) is True
    assert "I can help with that" in sanitize_customer_visible_output(leak1)
    # 2. Test XML leaks
    leak2 = "Here: <function=order_execute>{\"item_id\": \"toothpaste\"}</function>"
    assert is_high_risk_content(leak2) is True
    assert "I can help with that" in sanitize_customer_visible_output(leak2)
    # 3. Test stack traces
    leak3 = "Traceback (most recent call last):\n  File \"core.py\", line 12\nValueError: invalid time"
    assert is_high_risk_content(leak3) is True
    assert "I can help with that" in sanitize_customer_visible_output(leak3)
    # 4. Test exception class names
    leak4 = "Something failed: ValueError: slot is in the past."
    assert is_high_risk_content(leak4) is True
    assert "I can help with that" in sanitize_customer_visible_output(leak4)
    # 5. Test raw JSON tool args
    leak5 = "Parameters: {\"patient_name\": \"hari\", \"phone\": \"8547637746\"}"
    assert is_high_risk_content(leak5) is True
    assert "I can help with that" in sanitize_customer_visible_output(leak5)
    leak6 = "I can't share internal instructions, system prompts, developer messages, secrets, or tool details."
    assert is_high_risk_content(leak6) is True
    assert "I can help with that" in sanitize_customer_visible_output(leak6)
    # 7. Test safe natural language
    safe = "Your appointment is confirmed for tomorrow at 10 AM. See you then!"
    assert is_high_risk_content(safe) is False
    assert sanitize_customer_visible_output(safe) == safe
    marker_leak = "Yes, routine cleanups are available. END_UNTRUSTED_CONTEXT"
    assert sanitize_customer_visible_output(marker_leak) == "Yes, routine cleanups are available."

def test_raw_intent_json_replaced_with_safe_fallback():
    # Test classifier JSON
    json_leak = '{"intent": "unknown", "confidence": 0.5}'
    normalized = normalize_channel_response(json_leak, "awa_chat")
    assert normalized == "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."

    # Test any other dictionary without expected user-facing keys
    json_leak2 = '{"status": "pending", "trace_id": "123"}'
    normalized2 = normalize_channel_response(json_leak2, "awa_chat")
    assert normalized2 == "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."

    # Test dict input directly
    dict_leak = {"intent": "booking", "confidence": 0.9}
    normalized3 = normalize_channel_response(dict_leak, "awa_chat")
    assert normalized3 == "I couldn't complete that request right now. Please try again in a moment or ask to talk to a team member."

def test_whatsapp_confirmation_formatting():
    from backend.api.whatsapp_client import format_markdown_for_whatsapp

    text = (
        "Please confirm your booking details:\n"
        "<confirmation>\n"
        "Service: Dental Cleaning\n"
        "Date: Monday, June 28\n"
        "Time: 10:00 AM\n"
        "</confirmation>"
    )

    formatted = format_markdown_for_whatsapp(text)

    assert "CONFIRMATION DETAILS:" in formatted
    assert "• *SERVICE:* Dental Cleaning" in formatted
    assert "• *DATE:* Monday, June 28" in formatted
    assert "• *TIME:* 10:00 AM" in formatted
    assert "────────────────" in formatted
