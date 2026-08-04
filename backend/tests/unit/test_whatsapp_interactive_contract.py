from backend.pipeline.interactive_parser import (
    BUTTON_TITLE_LIMIT,
    LIST_OPTION_LIMIT,
    LIST_ROW_TITLE_LIMIT,
    build_plain_text_fallback,
    parse_interactive_hints,
)
from backend.pipeline.worker import _interactive_options
def test_whatsapp_buttons_success():
    clean, payload = parse_interactive_hints(
        "Choose one: [Confirm] [ Change ] [confirm]"
    )
    assert clean == "Choose one:"
    assert payload["type"] == "button"
    buttons = payload["action"]["buttons"]
    assert len(buttons) == 2
    assert buttons[0]["reply"]["title"] == "Confirm"
    assert buttons[1]["reply"]["title"] == "Change"
    assert buttons[0]["reply"]["id"].startswith("btn_0_")

def test_whatsapp_buttons_degrade_on_long_labels():
    clean, payload = parse_interactive_hints(
        "Choose one: [Confirm this very long booking label] [ Change ]"
    )
    assert "Choose one:" in clean
    assert "1. Confirm this very long booking label" in clean
    assert "2. Change" in clean
    assert payload is None

def test_whatsapp_list_success():
    labels = "".join(f" [Option {index}]" for index in range(5))
    clean, payload = parse_interactive_hints(f"Pick:{labels}")
    assert clean == "Pick:"
    assert payload["type"] == "list"
    rows = payload["action"]["sections"][0]["rows"]
    assert len(rows) == 5
    assert rows[0]["reply" if "reply" in rows[0] else "title"] == "Option 0"
    assert rows[0]["id"].startswith("list_0_")

def test_whatsapp_list_degrade_on_long_labels():
    labels = " [Option 0] [Option 1 with a very long label that exceeds limit] [Option 2] [Option 3]"
    clean, payload = parse_interactive_hints(f"Pick:{labels}")
    assert "Pick:" in clean
    assert "1. Option 0" in clean
    assert "2. Option 1 with a very long label that exceeds limit" in clean
    assert payload is None

def test_single_or_empty_options_stay_plain_text():
    clean, payload = parse_interactive_hints("You chose [Haircut].")
    assert clean == "You chose [Haircut]."
    assert payload is None
def test_plain_text_fallback_preserves_options_when_interactive_fails():
    fallback = build_plain_text_fallback("Please choose:", ["Confirm", "Change", "Cancel"])
    assert fallback == "Please choose:\n\nOptions: Confirm, Change, Cancel"
def test_worker_extracts_interactive_options_for_fallback():
    _, button_payload = parse_interactive_hints("Choose: [Confirm] [Change]")
    _, list_payload = parse_interactive_hints("Pick: [One] [Two] [Three] [Four]")
    assert _interactive_options(button_payload) == ["Confirm", "Change"]
    assert _interactive_options(list_payload) == ["One", "Two", "Three", "Four"]