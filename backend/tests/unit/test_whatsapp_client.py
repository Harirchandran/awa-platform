import pytest
from backend.api import whatsapp_client as whatsapp_module
from backend.api.whatsapp_client import WhatsAppClient
@pytest.mark.asyncio
async def test_mock_mode_send_text_does_not_require_meta_credentials(monkeypatch):
    monkeypatch.setattr(whatsapp_module.settings, "AWA_INTEGRATION_MODE", "mock")
    monkeypatch.setattr(whatsapp_module.settings, "WHATSAPP_MODE", "mock")
    client = WhatsAppClient()
    client.phone_number_id = None
    client.api_token = None
    result = await client.send_text_message("1234567890", "Hello from test")
    assert result["success"] is True
    assert result["mock"] is True
    assert result["message_id"].startswith("mock_whatsapp_text_")

@pytest.mark.asyncio
async def test_live_mode_send_text_does_not_fallback_to_global_credentials(monkeypatch):
    monkeypatch.setattr(whatsapp_module.settings, "AWA_INTEGRATION_MODE", "live")
    monkeypatch.setattr(whatsapp_module.settings, "WHATSAPP_MODE", "live")
    client = WhatsAppClient()
    client.phone_number_id = "global_phone_number_id"
    client.api_token = "global_access_token"

    missing_phone = await client.send_text_message("1234567890", "Hello from test")
    missing_token = await client.send_text_message(
        "1234567890",
        "Hello from test",
        phone_number_id="tenant_phone_number_id",
    )

    assert missing_phone["success"] is False
    assert missing_phone["error"] == "phone_number_id not configured"
    assert missing_token["success"] is False
    assert missing_token["error"] == "api token not configured"

def test_format_markdown_for_whatsapp():
    from backend.api.whatsapp_client import format_markdown_for_whatsapp

    # 1. Headings
    assert format_markdown_for_whatsapp("# Welcome") == "*WELCOME*"
    assert format_markdown_for_whatsapp("## hello there") == "*HELLO THERE*"

    # 2. Bold
    assert format_markdown_for_whatsapp("This is **bold** text.") == "This is *bold* text."
    assert format_markdown_for_whatsapp("This is __bold__ text.") == "This is *bold* text."

    # 3. Markdown Links
    assert format_markdown_for_whatsapp("Visit [AWA](https://awa.chat) now.") == "Visit AWA: https://awa.chat now."

    # 4. Tables and Pipes (Upgraded fallback)
    table_input = (
        "| Col1 | Col2 |\n"
        "|---|---|\n"
        "| Val1 | Val2 |"
    )
    expected_output = (
        "*COL1 | COL2*\n"
        "• Col1: Val1, Col2: Val2"
    )
    assert format_markdown_for_whatsapp(table_input) == expected_output

    # 5. Nested Lists (Indentation preservation)
    nested_list_input = (
        "- Parent Item\n"
        "  - Child Item"
    )
    expected_nested_list = (
        "• Parent Item\n"
        "  • Child Item"
    )
    assert format_markdown_for_whatsapp(nested_list_input) == expected_nested_list

    # 6. Italics
    assert format_markdown_for_whatsapp("This is *italic* text.") == "This is _italic_ text."
    assert format_markdown_for_whatsapp("This is _italic_ text.") == "This is _italic_ text."

    # 7. Code Blocks (Language specifier stripping)
    code_input = (
        "```python\n"
        "print('hello')\n"
        "```"
    )
    expected_code = (
        "```\n"
        "print('hello')\n"
        "```"
    )
    assert format_markdown_for_whatsapp(code_input) == expected_code

    # 8. Italics list safety (line-bounded regex test)
    # The previous greedy regex matched across lines, this test checks line-boundary safety.
    assert format_markdown_for_whatsapp("* Item 1\n* Item 2") == "• Item 1\n• Item 2"

    # 9. XML confirmation tags stripping
    xml_input = (
        "<confirmation>\n"
        "- Service: Haircut\n"
        "- Date: Today\n"
        "</confirmation>"
    )
    expected_xml = (
        "*────────────────*\n"
        "*CONFIRMATION DETAILS:*\n"
        "• *SERVICE:* Haircut\n"
        "• *DATE:* Today\n"
        "*────────────────*"
    )
    assert format_markdown_for_whatsapp(xml_input) == expected_xml
