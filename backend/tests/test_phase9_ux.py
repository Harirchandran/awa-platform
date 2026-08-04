"""
Phase 9 — UX & Pipeline Enhancements Test Suite
Tests all 9 Phase 5 enhancements without requiring live services.
"""
import asyncio
import json
import sys
import os
if "pytest" in sys.modules and __name__ != "__main__":
    import pytest
    pytest.skip("script-style Phase 9 smoke test; run with python -m backend.tests.test_phase9_ux", allow_module_level=True)
# Ensure parent dir is on path for 'backend' imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
# ==============================
# Test Results Tracker
# ==============================
PASSED = 0
FAILED = 0
ERRORS = []
def ok(name):
    global PASSED
    PASSED += 1
    print(f"  [PASS] {name}")
def fail(name, reason=""):
    global FAILED
    FAILED += 1
    ERRORS.append(f"{name}: {reason}")
    print(f"  [FAIL] {name} -- {reason}")
# ==============================
# TEST 1: Interactive Parser (Phase 9d)
# ==============================
print("\n=== TEST 1: Interactive Parser ===")
try:
    from backend.pipeline.interactive_parser import parse_interactive_hints
    # 1a: Button generation (≤3 options)
    clean, data = parse_interactive_hints("Which service? [Haircut] [Coloring] [Spa]")
    assert data is not None, "Expected interactive data"
    assert data["type"] == "button", f"Expected button, got {data['type']}"
    assert len(data["action"]["buttons"]) == 3, f"Expected 3 buttons, got {len(data['action']['buttons'])}"
    assert "Haircut" not in clean, "Brackets should be stripped from clean text"
    ok("Button generation (3 options)")
    # 1b: List generation (>3 options)
    clean2, data2 = parse_interactive_hints("Pick a day: [Mon] [Tue] [Wed] [Thu] [Fri]")
    assert data2 is not None, "Expected interactive data"
    assert data2["type"] == "list", f"Expected list, got {data2['type']}"
    assert len(data2["action"]["sections"][0]["rows"]) == 5
    ok("List generation (5 options)")
    # 1c: No options — returns None
    clean3, data3 = parse_interactive_hints("Hello, how can I help you?")
    assert data3 is None, "No brackets → should return None"
    ok("Plain text passthrough (no brackets)")
    # 1d: Single bracket — should NOT trigger (need ≥2)
    clean4, data4 = parse_interactive_hints("You chose [Haircut].")
    assert data4 is None, "Single bracket → should return None"
    ok("Single bracket ignored")
    # 1e: Dedup options
    clean5, data5 = parse_interactive_hints("Choose: [Yes] [No] [yes]")
    assert len(data5["action"]["buttons"]) == 2, "Duplicates should be removed"
    ok("Duplicate option deduplication")
except Exception as e:
    fail("Interactive Parser", str(e))
# ==============================
# TEST 2: Message Type Enum (Phase 9e)
# ==============================
print("\n=== TEST 2: MessageType Enum ===")
try:
    from backend.schemas.canonical_message import MessageType
    assert hasattr(MessageType, "STICKER"), "Missing STICKER"
    ok("STICKER enum exists")
    assert hasattr(MessageType, "CONTACTS"), "Missing CONTACTS"
    ok("CONTACTS enum exists")
    assert MessageType.STICKER.value == "sticker"
    ok("STICKER value correct")
    assert MessageType.CONTACTS.value == "contacts"
    ok("CONTACTS value correct")
    # All expected types
    expected = {"text", "image", "video", "audio", "document", "sticker", "location", "interactive", "contacts", "unknown"}
    actual = {m.value for m in MessageType}
    assert actual == expected, f"Mismatch: {actual.symmetric_difference(expected)}"
    ok("All 10 message types present")
except Exception as e:
    fail("MessageType Enum", str(e))
# ==============================
# TEST 3: Non-Text Message Extraction (Phase 9e)
# ==============================
print("\n=== TEST 3: Non-Text Message Extraction ===")
try:
    # Import the helper functions from webhook
    # They are module-level functions, not class methods
    from backend.api.webhook import _resolve_message_type, _extract_message_text
    # 3a: Text message
    assert _extract_message_text({"text": {"body": "Hello"}}, "text") == "Hello"
    ok("Text extraction")
    # 3b: Image with caption
    assert _extract_message_text({"image": {"caption": "My photo"}}, "image") == "My photo"
    ok("Image caption extraction")
    # 3c: Image without caption
    assert _extract_message_text({"image": {"id": "abc"}}, "image") == "[Image received]"
    ok("Image fallback (no caption)")
    # 3d: Sticker
    assert _extract_message_text({"sticker": {"id": "abc"}}, "sticker") == "[Sticker received]"
    ok("Sticker descriptor")
    # 3e: Location
    result = _extract_message_text({"location": {"latitude": 12.9, "longitude": 77.6, "name": "Bangalore"}}, "location")
    assert "Bangalore" in result
    ok("Location with name")
    # 3f: Button reply
    msg = {"interactive": {"type": "button_reply", "button_reply": {"id": "btn_1", "title": "Haircut"}}}
    assert _extract_message_text(msg, "interactive") == "Haircut"
    ok("Interactive button reply extraction")
    # 3g: List reply
    msg2 = {"interactive": {"type": "list_reply", "list_reply": {"id": "list_1", "title": "Monday"}}}
    assert _extract_message_text(msg2, "interactive") == "Monday"
    ok("Interactive list reply extraction")
    # 3h: Contact card
    assert _extract_message_text({}, "contacts") == "[Contact card received]"
    ok("Contact card descriptor")
    # 3i: Unknown type
    assert _extract_message_text({}, "reaction") == "[Unsupported message type]"
    ok("Unknown type fallback")
    # 3j: Resolve message type
    assert _resolve_message_type("text") == MessageType.TEXT
    assert _resolve_message_type("sticker") == MessageType.STICKER
    assert _resolve_message_type("contacts") == MessageType.CONTACTS
    assert _resolve_message_type("weird") == MessageType.UNKNOWN
    ok("Message type resolution")
except Exception as e:
    fail("Non-Text Message Extraction", str(e))
# ==============================
# TEST 4: Session Timeout Constant (Phase 9f)
# ==============================
print("\n=== TEST 4: Session Timeout ===")
try:
    from backend.pipeline.session_manager import SESSION_TIMEOUT_SECONDS, CACHE_TTL_SECONDS
    assert SESSION_TIMEOUT_SECONDS == 86400, f"Expected 86400, got {SESSION_TIMEOUT_SECONDS}"
    ok("Session timeout is 24 hours (86400s)")
    assert CACHE_TTL_SECONDS == 3600
    ok("Cache TTL is 1 hour (3600s)")
    assert SESSION_TIMEOUT_SECONDS > CACHE_TTL_SECONDS, "Timeout must be > cache TTL"
    ok("Timeout > Cache TTL invariant")
except Exception as e:
    fail("Session Timeout", str(e))
# ==============================
# TEST 5: Burst Buffer Constants (Phase 9c)
# ==============================
print("\n=== TEST 5: Burst Buffer ===")
try:
    from backend.pipeline.burst_buffer import (
        BURST_WINDOW_SECONDS,
        BURST_LIST_PREFIX,
        BURST_SENTINEL_PREFIX,
        BURST_ACTIVE_SET,
        BurstBuffer
    )
    assert BURST_WINDOW_SECONDS == 3
    ok("Burst window is 3 seconds")
    assert "burst:msgs:" in BURST_LIST_PREFIX
    ok("Burst list prefix correct")
    assert "burst:ttl:" in BURST_SENTINEL_PREFIX
    ok("Burst sentinel prefix correct")
    assert BURST_ACTIVE_SET == "burst:active"
    ok("Burst active set key correct")
    # Verify class methods exist
    assert hasattr(BurstBuffer, "add_message")
    assert hasattr(BurstBuffer, "get_expired_bursts")
    assert hasattr(BurstBuffer, "flush_burst")
    ok("BurstBuffer class has all required methods")
except Exception as e:
    fail("Burst Buffer", str(e))
# ==============================
# TEST 6: Data Purge Script (Phase 9g)
# ==============================
print("\n=== TEST 6: Data Purge ===")
try:
    from backend.ops.data_purge import run_purge
    import inspect
    assert inspect.iscoroutinefunction(run_purge), "run_purge must be async"
    ok("data_purge.run_purge is async callable")
except Exception as e:
    fail("Data Purge", str(e))
# ==============================
# TEST 7: Config Schema (Phase 9b)
# ==============================
print("\n=== TEST 7: Config Schema — ignored_numbers ===")
try:
    schema_path = os.path.join(os.path.dirname(__file__), "..", "..", "tenants", "_schema", "config_schema.json")
    with open(schema_path) as f:
        schema = json.load(f)
    props = schema.get("properties", {})
    assert "ignored_numbers" in props, "ignored_numbers not in config schema"
    ok("ignored_numbers property exists in schema")
    ig = props["ignored_numbers"]
    assert ig["type"] == "array"
    assert ig["items"]["type"] == "string"
    ok("ignored_numbers type is array of strings")
except Exception as e:
    fail("Config Schema", str(e))
# ==============================
# TEST 8: Docker Compose — burst-worker (Phase 9c)
# ==============================
print("\n=== TEST 8: Docker Compose ===")
try:
    compose_path = os.path.join(os.path.dirname(__file__), "..", "..", "docker-compose.yml")
    with open(compose_path) as f:
        content = f.read()
    assert "burst-worker" in content, "burst-worker service not in docker-compose.yml"
    ok("burst-worker service defined")
    assert "backend.pipeline.burst_worker" in content
    ok("burst-worker runs correct module")
except Exception as e:
    fail("Docker Compose", str(e))
# ==============================
# TEST 9: Webhook Imports (Phase 9a/b/h/i)
# ==============================
print("\n=== TEST 9: Webhook Module Imports ===")
try:
    from backend.api.webhook import receive_webhook, health_check, verify_webhook
    ok("Webhook endpoints importable")
    from backend.api.webhook import _resolve_message_type, _extract_message_text
    ok("Non-text helpers importable")
except Exception as e:
    fail("Webhook Imports", str(e))
# ==============================
# TEST 10: Worker Integration (Phase 9d)
# ==============================
print("\n=== TEST 10: Worker Integration ===")
try:
    from backend.pipeline.worker import process_message, _dispatch_response
    ok("Worker functions importable")
    # Verify interactive_parser is imported in worker
    import backend.pipeline.worker as wmod
    assert hasattr(wmod, "parse_interactive_hints"), "parse_interactive_hints not imported in worker"
    ok("Interactive parser integrated in worker")
except Exception as e:
    fail("Worker Integration", str(e))
# ==============================
# RESULTS
# ==============================
print(f"\n{'='*50}")
print(f"  RESULTS: {PASSED} passed, {FAILED} failed")
print(f"{'='*50}")
if ERRORS:
    print("\nFailures:")
    for e in ERRORS:
        print(f"  [FAIL] {e}")
sys.exit(0 if FAILED == 0 else 1)