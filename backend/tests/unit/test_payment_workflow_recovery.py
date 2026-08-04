"""
Unit tests for Slice 3C-3: payment active workflow semantic field extraction.

These tests use stubs for the LLM and tool registry so no network calls or
database access are made. All safety properties are verified deterministically.

Safety properties tested:
- LLM-extracted unsafe/blocked fields (payment_status, transaction_id, card_number,
  etc.) cause fail-closed rejection of the entire extraction result.
- Negative or zero amounts are always rejected.
- Credential-shaped customer text causes extract_payment_fields to raise without
  calling the LLM.
- Low-confidence and unknown-workflow extractions fall back to clarification.
- Payment state is NEVER mutated when extraction fails.
- Module scope guard prevents activation for non-payment sessions.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from backend.core.config import settings
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)


@pytest.fixture(autouse=True)
def explicit_legacy_behavior_runtime(monkeypatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False)
from backend.core.states import ConversationState
from backend.logic.engine.field_extraction import (
    PaymentFieldExtraction,
    _coerce_payment_extraction,
    _payment_text_contains_credential_shape,
    _PAYMENT_UNSAFE_FIELDS,
)

pytestmark = pytest.mark.anyio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payment_context(
    step_data: Optional[Dict[str, Any]] = None,
    current_module: str = "payments",
    incoming_text: str = "I want to pay 500 rupees",
    module_config: Optional[Dict[str, Any]] = None,
) -> ExecutionContext:
    tenant_id = uuid4()
    payments_cfg = {
        "enabled": True,
        "allowed_currencies": ["INR", "USD"],
        "default_currency": "INR",
        **(module_config or {}),
    }
    session_state = SessionState(
        workflow_state=ConversationState.COLLECTING_DATA,
        step_data={},
    )
    session_state.scoped_step_data["payments"] = dict(step_data or {})
    session_state.scoped_step_data["payment"] = dict(step_data or {})
    from backend.core.context import ActionLedger
    session_state.action_ledger = ActionLedger(current_task=current_module, task_status="collecting")

    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Shop", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Test"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module=current_module,
            state=session_state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={
                "business_name": "Shop",
                "modules": {"payments": payments_cfg},
            },
        ),
        incoming_message={"text": incoming_text},
    )



def _make_tool_def(module_obj: Any) -> Any:
    td = MagicMock()
    td.module = module_obj
    return td


def _make_payments_module(
    required_fields: Optional[list] = None,
    schema_properties: Optional[Dict[str, Any]] = None,
) -> Any:
    """Stub payments module that mimics the real PaymentsModule interface."""
    if required_fields is None:
        required_fields = [
            {"key": "amount", "type": "number", "required": True},
            {"key": "currency", "type": "string", "required": True},
            {"key": "payment_purpose", "type": "string", "required": False},
        ]
    if schema_properties is None:
        schema_properties = {
            "amount": {"type": "number"},
            "currency": {"type": "string"},
            "payment_purpose": {"type": "string"},
            "customer_name": {"type": "string"},
            "phone": {"type": "string"},
            "email": {"type": "string"},
            "notes": {"type": "string"},
        }

    module = MagicMock()
    module.get_required_fields_for_context = MagicMock(return_value=required_fields)
    module.get_tool_schema = MagicMock(
        return_value={"type": "object", "properties": schema_properties}
    )
    # validate_input: accept everything unless it's a blocked field
    def _validate_input(field_def, value, tenant_config=None):
        result = MagicMock()
        result.is_valid = True
        return result
    module.validate_input = MagicMock(side_effect=_validate_input)
    return module


# ---------------------------------------------------------------------------
# _coerce_payment_extraction unit tests
# ---------------------------------------------------------------------------

class TestCoercePaymentExtraction:
    """Tests for the coerce function that processes raw LLM JSON output."""

    def test_safe_fields_accepted(self):
        payload = {
            "workflow": "payment",
            "workflow_action": "continue",
            "confidence": 0.9,
            "field_updates": {"amount": 500, "currency": "INR"},
            "field_confidence": {"amount": 0.92, "currency": 0.88},
            "clarification_needed": False,
            "clarification_fields": [],
        }
        result = _coerce_payment_extraction(payload)
        assert result.workflow == "payment"
        assert result.workflow_action == "continue"
        assert result.confidence == 0.9
        assert result.field_updates == {"amount": 500, "currency": "INR"}
        assert result.clarification_needed is False

    def test_unsafe_field_in_updates_rejects_entire_result(self):
        """Any unsafe field causes the entire extraction to be reset — fail-closed."""
        for unsafe_key in [
            "payment_status", "status", "paid", "captured", "failed",
            "transaction_id", "card_number", "cvv", "otp", "api_key",
        ]:
            payload = {
                "workflow": "payment",
                "workflow_action": "continue",
                "confidence": 0.95,
                "field_updates": {
                    "amount": 100,
                    unsafe_key: "attacker_value",
                },
                "field_confidence": {},
                "clarification_needed": False,
                "clarification_fields": [],
            }
            result = _coerce_payment_extraction(payload)
            assert result.workflow == "unknown", f"Expected unknown for unsafe key: {unsafe_key}"
            assert result.confidence == 0.0, f"Expected confidence 0 for unsafe key: {unsafe_key}"
            assert result.field_updates == {}, f"Expected empty updates for unsafe key: {unsafe_key}"

    def test_invalid_workflow_value_coerced_to_unknown(self):
        payload = {
            "workflow": "booking",  # Wrong workflow type
            "workflow_action": "continue",
            "confidence": 0.9,
            "field_updates": {"amount": 100},
        }
        result = _coerce_payment_extraction(payload)
        assert result.workflow == "unknown"

    def test_invalid_workflow_action_coerced_to_unknown(self):
        payload = {
            "workflow": "payment",
            "workflow_action": "confirm_and_execute",  # Not a valid action
            "confidence": 0.9,
            "field_updates": {"amount": 100},
        }
        result = _coerce_payment_extraction(payload)
        assert result.workflow_action == "unknown"

    def test_confidence_clamped_to_range(self):
        for raw_conf, expected in [(-0.5, 0.0), (2.0, 1.0), (0.8, 0.8)]:
            payload = {
                "workflow": "payment",
                "workflow_action": "continue",
                "confidence": raw_conf,
                "field_updates": {},
            }
            result = _coerce_payment_extraction(payload)
            assert result.confidence == expected

    def test_non_dict_field_updates_treated_as_empty(self):
        payload = {
            "workflow": "payment",
            "workflow_action": "continue",
            "confidence": 0.8,
            "field_updates": ["amount", "100"],  # Wrong type
        }
        result = _coerce_payment_extraction(payload)
        assert result.field_updates == {}


# ---------------------------------------------------------------------------
# Credential-shape detection tests
# ---------------------------------------------------------------------------

class TestCredentialShapeDetection:
    """Tests for _payment_text_contains_credential_shape."""

    def test_long_digit_sequence_detected_as_card_like(self):
        assert _payment_text_contains_credential_shape("4111111111111111") is True
        assert _payment_text_contains_credential_shape("my card is 4111111111111111") is True

    def test_cvv_label_with_digits_detected(self):
        assert _payment_text_contains_credential_shape("cvv=123") is True
        assert _payment_text_contains_credential_shape("CVV: 456") is True
        assert _payment_text_contains_credential_shape("cvc 789") is True

    def test_otp_label_with_digits_detected(self):
        assert _payment_text_contains_credential_shape("otp=123456") is True
        assert _payment_text_contains_credential_shape("OTP: 654321") is True

    def test_upi_uri_detected(self):
        assert _payment_text_contains_credential_shape("upi://merchant@bank") is True

    def test_normal_payment_text_not_detected(self):
        assert _payment_text_contains_credential_shape("I want to pay 500 rupees") is False
        assert _payment_text_contains_credential_shape("Pay via UPI please") is False
        assert _payment_text_contains_credential_shape("My amount is 100 USD") is False

    def test_12_digit_number_not_detected_as_card(self):
        # Less than 13 digits — not blocked
        assert _payment_text_contains_credential_shape("123456789012") is False

    def test_pin_label_with_digits_detected(self):
        assert _payment_text_contains_credential_shape("pin=9876") is True


# ---------------------------------------------------------------------------
# _apply_safe_payment_field_updates tests
# ---------------------------------------------------------------------------

class TestApplySafePaymentFieldUpdates:
    """Tests for the field-application method on AgenticEngine."""

    def _engine(self):
        from backend.logic.engine.core import AgenticEngine
        engine = AgenticEngine.__new__(AgenticEngine)
        # Stub out tool_registry to avoid real module loading
        engine.tool_registry = MagicMock()
        return engine

    def _extraction(self, **kwargs) -> PaymentFieldExtraction:
        defaults = {
            "workflow": "payment",
            "workflow_action": "continue",
            "confidence": 0.85,
            "field_updates": {"amount": 500.0, "currency": "INR"},
            "field_confidence": {"amount": 0.90, "currency": 0.88},
            "clarification_needed": False,
            "clarification_fields": [],
        }
        defaults.update(kwargs)
        return PaymentFieldExtraction(**defaults)

    def test_valid_amount_and_currency_applied(self):
        engine = self._engine()
        context = _payment_context(step_data={})
        module = _make_payments_module()
        tool_def = _make_tool_def(module)
        extraction = self._extraction()

        with patch("backend.logic.engine.core.ToolRegistry") as MockReg:
            MockReg._strict_tool_schema.return_value = {
                "properties": {
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                }
            }
            changed, invalid = engine._apply_safe_payment_field_updates(context, extraction, tool_def)

        assert invalid is None
        assert changed is True
        step = context.session_data.state.get_task_step_data("payments")
        assert float(step.get("amount", 0)) == 500.0
        assert step.get("currency") == "INR"

    def test_negative_amount_rejected(self):
        engine = self._engine()
        context = _payment_context(step_data={})
        module = _make_payments_module()
        tool_def = _make_tool_def(module)
        extraction = self._extraction(
            field_updates={"amount": -50.0},
            field_confidence={"amount": 0.95},
        )

        with patch("backend.logic.engine.core.ToolRegistry") as MockReg:
            MockReg._strict_tool_schema.return_value = {"properties": {"amount": {"type": "number"}}}
            changed, invalid = engine._apply_safe_payment_field_updates(context, extraction, tool_def)

        assert invalid == "amount"
        assert changed is False
        # State should be untouched
        step = context.session_data.state.get_task_step_data("payments")
        assert "amount" not in step or step["amount"] not in (-50.0, "-50.0")

    def test_zero_amount_rejected(self):
        engine = self._engine()
        context = _payment_context(step_data={})
        module = _make_payments_module()
        tool_def = _make_tool_def(module)
        extraction = self._extraction(
            field_updates={"amount": 0},
            field_confidence={"amount": 0.95},
        )

        with patch("backend.logic.engine.core.ToolRegistry") as MockReg:
            MockReg._strict_tool_schema.return_value = {"properties": {"amount": {"type": "number"}}}
            changed, invalid = engine._apply_safe_payment_field_updates(context, extraction, tool_def)

        assert invalid == "amount"
        assert changed is False

    def test_disallowed_currency_rejected(self):
        engine = self._engine()
        context = _payment_context(step_data={})
        module = _make_payments_module()
        tool_def = _make_tool_def(module)
        extraction = self._extraction(
            field_updates={"currency": "GBP"},  # Not in allowed_currencies
            field_confidence={"currency": 0.95},
        )

        with patch("backend.logic.engine.core.ToolRegistry") as MockReg:
            MockReg._strict_tool_schema.return_value = {"properties": {"currency": {"type": "string"}}}
            changed, invalid = engine._apply_safe_payment_field_updates(context, extraction, tool_def)

        assert invalid == "currency"
        assert changed is False

    def test_blocked_field_in_updates_rejects_all(self):
        engine = self._engine()
        context = _payment_context(step_data={})
        module = _make_payments_module()
        tool_def = _make_tool_def(module)

        for blocked_key in ["payment_status", "transaction_id", "card_number", "api_key"]:
            extraction = self._extraction(
                field_updates={blocked_key: "some_value", "amount": 100.0},
                field_confidence={blocked_key: 0.99, "amount": 0.90},
            )
            with patch("backend.logic.engine.core.ToolRegistry") as MockReg:
                MockReg._strict_tool_schema.return_value = {
                    "properties": {blocked_key: {"type": "string"}, "amount": {"type": "number"}}
                }
                changed, invalid = engine._apply_safe_payment_field_updates(context, extraction, tool_def)

            assert changed is False, f"Should not apply when blocked key present: {blocked_key}"
            assert invalid is not None, f"Should report invalid field for: {blocked_key}"

    def test_low_confidence_field_skipped(self):
        engine = self._engine()
        context = _payment_context(step_data={})
        module = _make_payments_module()
        tool_def = _make_tool_def(module)
        # Per-field confidence below PAYMENT_FIELD_CONFIDENCE_THRESHOLD (0.75)
        extraction = self._extraction(
            field_updates={"amount": 500.0, "currency": "INR"},
            field_confidence={"amount": 0.50, "currency": 0.50},
            confidence=0.80,
        )

        with patch("backend.logic.engine.core.ToolRegistry") as MockReg:
            MockReg._strict_tool_schema.return_value = {
                "properties": {"amount": {"type": "number"}, "currency": {"type": "string"}}
            }
            changed, invalid = engine._apply_safe_payment_field_updates(context, extraction, tool_def)

        # All fields skipped due to low per-field confidence
        assert changed is False
        assert invalid is None

    def test_state_not_mutated_on_failure(self):
        """Payment state must never be mutated when extraction fails."""
        engine = self._engine()
        # Use scoped_step_data (the path the engine uses via get_task_step_data)
        tenant_id = uuid4()
        payments_cfg = {"enabled": True, "allowed_currencies": ["INR", "USD"]}
        session_state = SessionState(
            workflow_state=ConversationState.COLLECTING_DATA,
            step_data={},
        )
        # Seed scoped state directly
        session_state.scoped_step_data["payments"] = {"amount": "200", "currency": "INR"}
        from backend.core.context import ActionLedger
        session_state.action_ledger = ActionLedger(current_task="payments", task_status="collecting")
        context = ExecutionContext(
            tenant=Tenant(id=tenant_id, name="Shop", plan_code="pro", status="active"),
            user=User(id=uuid4(), phone_number="+919999999999", name="Test"),
            session_data=ConversationSession(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=uuid4(),
                config_version=1,
                current_module="payments",
                state=session_state,
            ),
            config=TenantConfig(
                tenant_id=tenant_id,
                version=1,
                config_data={"business_name": "Shop", "modules": {"payments": payments_cfg}},
            ),
            incoming_message={"text": "pay for order"},
        )

        module = _make_payments_module()
        tool_def = _make_tool_def(module)
        # Extraction with a blocked field
        extraction = self._extraction(
            field_updates={"payment_status": "paid", "amount": 999.0},
            field_confidence={"payment_status": 0.99, "amount": 0.95},
        )

        with patch("backend.logic.engine.core.ToolRegistry") as MockReg:
            MockReg._strict_tool_schema.return_value = {
                "properties": {"amount": {"type": "number"}, "payment_status": {"type": "string"}}
            }
            changed, invalid = engine._apply_safe_payment_field_updates(context, extraction, tool_def)

        assert changed is False
        step = context.session_data.state.get_task_step_data("payments")
        assert step.get("amount") == "200", "Existing amount must not be overwritten on failure"
        assert step.get("currency") == "INR", "Existing currency must not be overwritten on failure"
        assert "payment_status" not in step, "Blocked field must never be written to state"


# ---------------------------------------------------------------------------
# Module scope guard tests
# ---------------------------------------------------------------------------

class TestPaymentModuleScopeGuard:
    """_handle_semantic_payment_field_extraction must be a no-op for non-payment sessions."""

    @pytest.mark.anyio
    async def test_no_op_for_non_payment_module(self):
        from backend.logic.engine.core import AgenticEngine

        engine = AgenticEngine.__new__(AgenticEngine)
        engine.tool_registry = MagicMock()

        # Session where current_module is "bookings", not payments
        context = _payment_context(current_module="bookings", incoming_text="any")
        routing_decision = SimpleNamespace(intent="booking", route_type="module_route", reason="")

        result = await engine._handle_semantic_payment_field_extraction(context, routing_decision)
        assert result is None, "Should be a no-op when not in a payment workflow scope"

    @pytest.mark.anyio
    async def test_no_op_when_module_disabled(self):
        from backend.logic.engine.core import AgenticEngine

        engine = AgenticEngine.__new__(AgenticEngine)
        engine.tool_registry = MagicMock()

        context = _payment_context(module_config={"enabled": False})
        routing_decision = SimpleNamespace(intent="payments", route_type="module_route", reason="")

        result = await engine._handle_semantic_payment_field_extraction(context, routing_decision)
        assert result is None, "Should be a no-op when payments module is disabled"

    @pytest.mark.anyio
    async def test_no_op_for_faq_direct_answer(self):
        from backend.logic.engine.core import AgenticEngine

        engine = AgenticEngine.__new__(AgenticEngine)
        engine.tool_registry = MagicMock()

        context = _payment_context()
        routing_decision = SimpleNamespace(intent="faq", route_type="direct_answer", reason="")

        result = await engine._handle_semantic_payment_field_extraction(context, routing_decision)
        assert result is None, "Should not run semantic extraction for FAQ direct answers"


# ---------------------------------------------------------------------------
# extract_payment_fields credential guard test
# ---------------------------------------------------------------------------

class TestExtractPaymentFieldsCredentialGuard:
    """Credential-shape detection causes extract_payment_fields to raise without LLM call."""

    @pytest.mark.anyio
    async def test_card_number_in_message_raises_without_llm(self):
        from backend.logic.engine.field_extraction import extract_payment_fields

        context = _payment_context(incoming_text="4111111111111111 is my card number")
        llm_mock = AsyncMock()

        with patch("backend.logic.engine.llm.get_llm", return_value=llm_mock):
            with pytest.raises(ValueError, match="credential-shape"):
                await extract_payment_fields(context)

        llm_mock.ainvoke.assert_not_called()

    @pytest.mark.anyio
    async def test_otp_pattern_in_message_raises_without_llm(self):
        from backend.logic.engine.field_extraction import extract_payment_fields

        context = _payment_context(incoming_text="otp=654321 please verify")
        llm_mock = AsyncMock()

        with patch("backend.logic.engine.llm.get_llm", return_value=llm_mock):
            with pytest.raises(ValueError, match="credential-shape"):
                await extract_payment_fields(context)

        llm_mock.ainvoke.assert_not_called()

    @pytest.mark.anyio
    async def test_safe_message_calls_llm(self):
        from backend.logic.engine.field_extraction import extract_payment_fields

        context = _payment_context(incoming_text="I want to pay 500 INR for my order")

        llm_response = MagicMock()
        llm_response.content = '{"workflow":"payment","workflow_action":"continue","confidence":0.88,"field_updates":{"amount":500,"currency":"INR"},"field_confidence":{"amount":0.90,"currency":0.85},"clarification_needed":false,"clarification_fields":[]}'

        llm_mock = AsyncMock()
        llm_mock.ainvoke = AsyncMock(return_value=llm_response)

        with patch("backend.logic.engine.llm.get_llm", return_value=llm_mock), \
             patch("backend.logic.engine.field_extraction.asyncio.wait_for", return_value=llm_response), \
             patch("backend.logic.engine.field_extraction.record_langchain_usage"):
            result = await extract_payment_fields(context)

        assert result.workflow == "payment"
        assert result.field_updates.get("amount") == 500
        assert result.field_updates.get("currency") == "INR"


# ---------------------------------------------------------------------------
# Unsafe fields blocklist completeness test
# ---------------------------------------------------------------------------

class TestUnsafeFieldsBlocklist:
    """Verify the unsafe fields set covers all required sensitive payment fields."""

    def test_status_fields_blocked(self):
        for field in ["payment_status", "status", "paid", "success", "captured", "failed", "refunded"]:
            assert field in _PAYMENT_UNSAFE_FIELDS, f"Missing from blocklist: {field}"

    def test_provider_fields_blocked(self):
        for field in ["provider_payment_id", "provider_order_id", "transaction_id", "signature",
                      "webhook_payload", "refund_id", "settlement_id", "payment_link_url"]:
            assert field in _PAYMENT_UNSAFE_FIELDS, f"Missing from blocklist: {field}"

    def test_internal_id_fields_blocked(self):
        for field in ["tenant_id", "user_id", "customer_id", "order_id", "booking_id", "invoice_id"]:
            assert field in _PAYMENT_UNSAFE_FIELDS, f"Missing from blocklist: {field}"

    def test_credential_fields_blocked(self):
        for field in ["card_number", "cvv", "upi_pin", "otp", "bank_password",
                      "provider_credential", "api_key", "webhook_secret"]:
            assert field in _PAYMENT_UNSAFE_FIELDS, f"Missing from blocklist: {field}"


# ---------------------------------------------------------------------------
# Slice 3C-3 verification: False positive, unsafe-field, success/link, and redaction audits
# ---------------------------------------------------------------------------

class TestCredentialFalsePositives:
    """Verifies that non-credential business/payment inputs are NOT falsely blocked."""

    def test_indian_phone_number_not_blocked(self):
        assert _payment_text_contains_credential_shape("+919999999999") is False
        assert _payment_text_contains_credential_shape("9876543210") is False

    def test_normal_order_reference_not_blocked(self):
        assert _payment_text_contains_credential_shape("order-123456") is False
        assert _payment_text_contains_credential_shape("ORD-987654321") is False

    def test_normal_invoice_reference_not_blocked(self):
        assert _payment_text_contains_credential_shape("INV-9876543210") is False
        assert _payment_text_contains_credential_shape("invoice 5555") is False

    def test_normal_booking_reference_not_blocked(self):
        assert _payment_text_contains_credential_shape("BK-88889999") is False

    def test_normal_amounts_not_blocked(self):
        assert _payment_text_contains_credential_shape("5000") is False
        assert _payment_text_contains_credential_shape("12000") is False
        assert _payment_text_contains_credential_shape("999.50") is False

    def test_normal_upi_id_not_blocked(self):
        # UPI VPAs (Virtual Payment Addresses) are not PINs/credentials
        assert _payment_text_contains_credential_shape("someone@okaxis") is False
        assert _payment_text_contains_credential_shape("merchant@upi") is False

    def test_normal_email_address_not_blocked(self):
        assert _payment_text_contains_credential_shape("customer@domain.com") is False

    def test_normal_payment_purpose_not_blocked(self):
        assert _payment_text_contains_credential_shape("payment for consultation") is False


class TestUUIDFailClosed:
    """Verifies that UUIDs are rejected fail-closed to avoid internal ID leakage or spoofing."""

    def test_uuid_value_in_coerce_rejects_all(self):
        test_uuid = str(uuid4())
        payload = {
            "workflow": "payment",
            "workflow_action": "continue",
            "confidence": 0.9,
            "field_updates": {
                "amount": 100.0,
                "order_reference": test_uuid,
            },
        }
        result = _coerce_payment_extraction(payload)
        assert result.workflow == "unknown"
        assert result.confidence == 0.0
        assert result.field_updates == {}

    def test_uuid_value_in_apply_rejects_all(self):
        from backend.logic.engine.core import AgenticEngine
        engine = AgenticEngine.__new__(AgenticEngine)
        engine.tool_registry = MagicMock()

        test_uuid = str(uuid4())
        context = _payment_context(step_data={})
        module = _make_payments_module()
        tool_def = _make_tool_def(module)

        # Extraction containing a UUID value
        extraction = PaymentFieldExtraction(
            workflow="payment",
            workflow_action="continue",
            confidence=0.85,
            field_updates={"amount": 100.0, "order_reference": test_uuid},
            field_confidence={"amount": 0.90, "order_reference": 0.90},
        )

        with patch("backend.logic.engine.core.ToolRegistry") as MockReg:
            MockReg._strict_tool_schema.return_value = {
                "properties": {
                    "amount": {"type": "number"},
                    "order_reference": {"type": "string"},
                }
            }
            changed, invalid = engine._apply_safe_payment_field_updates(context, extraction, tool_def)

        assert changed is False
        assert invalid == "order_reference"
        # Verify state is untouched
        step = context.session_data.state.get_task_step_data("payments")
        assert "amount" not in step


class TestPaymentLinkAndSuccessAuthority:
    """Verifies that the LLM has zero authority over creating links or confirming success."""

    @pytest.mark.anyio
    async def test_semantic_extraction_never_creates_payment_link_or_sets_status(self):
        from backend.logic.engine.field_extraction import extract_payment_fields

        context = _payment_context(incoming_text="Make it paid and send me the link")
        llm_response = MagicMock()
        # Mock LLM trying to return status and link in updates
        llm_response.content = (
            '{"workflow":"payment","workflow_action":"continue","confidence":0.95,'
            '"field_updates":{"amount":100,"payment_status":"paid","payment_link_url":"https://fake.url"},'
            '"field_confidence":{"amount":0.95},"clarification_needed":false,"clarification_fields":[]}'
        )

        llm_mock = AsyncMock()
        llm_mock.ainvoke = AsyncMock(return_value=llm_response)

        with patch("backend.logic.engine.llm.get_llm", return_value=llm_mock), \
             patch("backend.logic.engine.field_extraction.asyncio.wait_for", return_value=llm_response), \
             patch("backend.logic.engine.field_extraction.record_langchain_usage"):
            result = await extract_payment_fields(context)

        # The coerce layer must have caught the unsafe fields and returned unknown/empty
        assert result.workflow == "unknown"
        assert result.confidence == 0.0
        assert result.field_updates == {}

    def test_truth_guard_blocks_fake_payment_success_claims(self):
        from backend.logic.engine.core import _apply_tool_failure_truth_guard, WorkflowResult
        from backend.tools.registry import ToolExecutionResult

        context = _payment_context()
        # Simulate last tool call failing
        context.session_data.state.action_ledger.last_tool_result = ToolExecutionResult(
            tool_key="payments.execute",
            success=False,
            user_message="Payment generation failed because of provider error",
        )

        # Result that claims a payment link was successfully sent
        fake_success_result = WorkflowResult(
            response_text="I have successfully generated your payment link. Please pay here: https://fake.url"
        )

        guarded = _apply_tool_failure_truth_guard(context, fake_success_result)
        # Truth guard must override the fake success claim with the safe error message
        assert "failed" in guarded.response_text or "couldn't complete" in guarded.response_text
        assert "https://fake.url" not in guarded.response_text


class TestPromptPayloadRedaction:
    """Verifies that the LLM extraction payload contains only safe, public labels/config."""

    def test_payload_contains_no_sensitive_ids_or_credentials(self):
        from backend.logic.engine.field_extraction import _payment_extraction_payload

        # Prepare context with internal IDs in state
        context = _payment_context(
            step_data={
                "amount": "200",
                "currency": "INR",
                "payment_status": "draft",
                "provider_payment_id": "pay_XYZ123",
                "tenant_id": str(uuid4()),
            }
        )

        payload = _payment_extraction_payload(context)

        # Check raw text keys in payload root
        assert "customer_message" in payload
        assert "language_context" in payload
        assert "workflow_scope" in payload
        assert "payment_config" in payload
        assert "current_payment_state" in payload

        # Verify state is redacted/filtered
        state_sent = payload["current_payment_state"]
        assert "amount" in state_sent
        assert "currency" in state_sent
        # Sensitive fields must not be present in LLM payload at all
        assert "payment_status" not in state_sent
        assert "provider_payment_id" not in state_sent
        assert "tenant_id" not in state_sent
