"""Iteration 7D-B tenant-aware terminal lead payload validation tests.

These tests cover the pure, validation-only terminal lead payload validator
``validate_terminal_configured_payload``. The validator resolves the exact
tenant-configured lead schema, normalizes and validates every supplied configured
value, and returns a typed immutable result. It performs no mutation, executes no
tool, invokes no registry, and touches no handler, service, repository, or event
layer. No live providers are contacted.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any

from unittest.mock import MagicMock, patch

import pytest
from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.states import ConversationState
from backend.domains.leads.tool_definitions import get_lead_capture_tool_schema
from backend.modules.lead_capture_module import (
    lead_capture_module,
    LeadTerminalPayloadValidationResult,
)

pytestmark = pytest.mark.anyio


EXPLICIT_TENANT_FIELDS = [
    {"key": "company_name", "label": "Company", "type": "string", "required": True},
    {"key": "contact_email", "label": "Contact Email", "type": "email", "required": True},
    {"key": "industry", "label": "Industry", "type": "string", "required": False},
]


def _make_context(*, fields=None, step_data=None):
    tenant_id = uuid.uuid4()
    modules = {
        "lead_capture": {
            "enabled": True,
            "fields": fields if fields is not None else EXPLICIT_TENANT_FIELDS,
        }
    }
    state = SessionState(workflow_state=ConversationState.COLLECTING_DATA)
    state.action_ledger.current_task = "lead_capture"
    state.action_ledger.task_status = "collecting"
    if step_data is not None:
        state.update_task_step_data("lead_capture", dict(step_data))
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Cafe", plan_code="enterprise", status="active"),
        user=User(id=uuid.uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid.uuid4(),
            config_version=1,
            current_module="lead_capture",
            state=state,
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data={"timezone": "Asia/Kolkata", "modules": modules},
        ),
        incoming_message={"text": "ping", "channel_type": "awa_chat", "source_type": "awa_chat"},
    )


# T1 -- Explicit tenant schema exact
def test_t1_explicit_tenant_schema_exact():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"company_name": "Acme"})
    assert res.configured_field_keys == ("company_name", "contact_email", "industry")


# T2 -- No hidden defaults
def test_t2_no_hidden_defaults():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"company_name": "Acme"})
    for forbidden in ("name", "customer_name", "phone", "email", "interest", "preferred_contact_time", "message", "notes"):
        assert forbidden not in res.configured_field_keys


# T3 -- Empty configuration fallback
def test_t3_empty_configuration_fallback():
    ctx = _make_context(fields=[])
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"name": "Arjun"})
    assert res.configured_field_keys == ("name", "interest")
    assert res.required_field_keys == ("name", "interest")


# T4 -- Complete custom payload valid
def test_t4_complete_custom_payload_valid():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    assert res.valid is True
    assert res.missing_required_field_keys == ()
    assert res.invalid_field_keys == ()
    assert res.reason_code == "complete"


# T5 -- Complete payload with optional custom field
def test_t5_optional_custom_field_preserved():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example", "industry": "SaaS"}
    )
    assert res.valid is True
    assert res.normalized_payload.get("industry") == "SaaS"
    assert "industry" in res.normalized_payload


# T6 -- Missing required custom field
def test_t6_missing_required_custom_field():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"company_name": "Acme"})
    assert res.valid is False
    assert res.missing_required_field_keys == ("contact_email",)


# T7 -- Multiple missing required fields ordered
def test_t7_multiple_missing_required_ordered():
    multi = [
        {"key": "company_name", "label": "Company", "type": "string", "required": True},
        {"key": "contact_email", "label": "Contact Email", "type": "email", "required": True},
        {"key": "industry", "label": "Industry", "type": "string", "required": True},
    ]
    ctx = _make_context(fields=multi)
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {})
    assert res.valid is False
    assert res.missing_required_field_keys == ("company_name", "contact_email", "industry")


# T8 -- Invalid configured email
def test_t8_invalid_configured_email():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "not-an-email"}
    )
    assert res.valid is False
    assert "contact_email" in res.invalid_field_keys


# T9 -- Valid configured phone
def test_t9_valid_configured_phone():
    fields = [
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"phone": "+919999999999"})
    assert res.valid is True


# T10 -- Invalid configured phone
def test_t10_invalid_configured_phone():
    fields = [
        {"key": "phone", "label": "Phone", "type": "phone", "required": True},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"phone": "abc"})
    assert res.valid is False
    assert "phone" in res.invalid_field_keys


# T11 -- Valid string
def test_t11_valid_string():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    assert res.valid is True


# T12 -- Invalid empty string
def test_t12_invalid_empty_string():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": " ", "contact_email": "sales@acme.example"}
    )
    assert res.valid is False
    assert "company_name" in res.invalid_field_keys


# T13 -- Invalid one-character string
def test_t13_invalid_one_char_string():
    ctx = _make_context(fields=[{"key": "name", "label": "Name", "type": "string", "required": True}])
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"name": "A"})
    assert res.valid is False
    assert "name" in res.invalid_field_keys


# T14 -- Textarea uses string validation
def test_t14_textarea_uses_string_validation():
    fields = [
        {"key": "notes", "label": "Notes", "type": "textarea", "required": True},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"notes": "A"})
    assert res.valid is False
    assert "notes" in res.invalid_field_keys
    res2 = lead_capture_module.validate_terminal_configured_payload(ctx, {"notes": "Enough text here"})
    assert res2.valid is True


# T15 -- Invalid nested dictionary
def test_t15_invalid_nested_dictionary():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": {"x": 1}, "contact_email": "sales@acme.example"}
    )
    assert res.valid is False
    assert "company_name" in res.invalid_field_keys


# T16 -- Invalid list value
def test_t16_invalid_list_value():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": ["Acme"], "contact_email": "sales@acme.example"}
    )
    assert res.valid is False
    assert "company_name" in res.invalid_field_keys


# T17 -- Unknown key rejected
def test_t17_unknown_key_rejected():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example", "unknown_field": "x"}
    )
    assert res.valid is False
    assert "unknown_field" not in res.normalized_payload
    assert "unknown_field" in res.rejected_field_keys


# T18 -- Protected key rejected
def test_t18_protected_key_rejected():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example", "tenant_id": "ABC"}
    )
    assert res.valid is False
    assert "tenant_id" not in res.normalized_payload
    assert "tenant_id" in res.rejected_field_keys
    assert res.reason_code == "protected_fields"


# T19 -- Consent-sensitive key rejected
def test_t19_consent_sensitive_key_rejected():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example", "marketing_consent": "yes"}
    )
    assert res.valid is False
    assert "marketing_consent" not in res.normalized_payload
    assert "marketing_consent" in res.rejected_field_keys
    assert res.reason_code == "consent_sensitive_fields"


# T20 -- Required unsupported type
def test_t20_required_unsupported_type():
    fields = [
        {"key": "age", "label": "Age", "type": "number", "required": True},
        {"key": "company_name", "label": "Company", "type": "string", "required": False},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(ctx, {"age": 30, "company_name": "Acme"})
    assert res.valid is False
    assert "age" in res.rejected_field_keys
    assert res.reason_code == "unsupported_configured_type"


# T21 -- Optional unsupported type omitted
def test_t21_optional_unsupported_type_omitted():
    fields = [
        {"key": "company_name", "label": "Company", "type": "string", "required": True},
        {"key": "contact_email", "label": "Contact Email", "type": "email", "required": True},
        {"key": "budget", "label": "Budget", "type": "number", "required": False},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    assert res.valid is True
    assert "budget" not in res.missing_required_field_keys


# T22 -- Optional unsupported type supplied
def test_t22_optional_unsupported_type_supplied():
    fields = [
        {"key": "company_name", "label": "Company", "type": "string", "required": True},
        {"key": "contact_email", "label": "Contact Email", "type": "email", "required": True},
        {"key": "budget", "label": "Budget", "type": "number", "required": False},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example", "budget": 100}
    )
    assert res.valid is False
    assert "budget" in res.rejected_field_keys


# T23 -- Optional field omitted
def test_t23_optional_field_omitted():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    assert res.valid is True
    assert "industry" not in res.missing_required_field_keys


# T24 -- Invalid supplied optional field
def test_t24_invalid_supplied_optional_field():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example", "industry": []}
    )
    assert res.valid is False
    assert "industry" in res.invalid_field_keys


# T25 -- Exact case-sensitive tenant keys
def test_t25_exact_case_sensitive_keys():
    fields = [
        {"key": "CompanyName", "label": "Company", "type": "string", "required": True},
        {"key": "ContactEmail", "label": "Contact Email", "type": "email", "required": True},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"CompanyName": "Acme", "ContactEmail": "sales@acme.example"}
    )
    assert res.valid is True
    assert "CompanyName" in res.normalized_payload
    assert "ContactEmail" in res.normalized_payload
    assert "companyname" not in res.normalized_payload


# T26 -- Name alias projection
def test_t26_name_alias_projection():
    fields = [
        {"key": "name", "label": "Name", "type": "string", "required": True},
        {"key": "interest", "label": "Interest", "type": "string", "required": True},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"customer_name": "Arjun", "interest": "demo"}
    )
    assert res.valid is True
    assert "name" in res.normalized_payload
    assert res.normalized_payload["name"] == "Arjun"
    assert "customer_name" not in res.normalized_payload


# T27 -- Customer-name alias projection
def test_t27_customer_name_alias_projection():
    fields = [
        {"key": "customer_name", "label": "Customer Name", "type": "string", "required": True},
        {"key": "interest", "label": "Interest", "type": "string", "required": True},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"name": "Arjun", "interest": "demo"}
    )
    assert res.valid is True
    assert "customer_name" in res.normalized_payload
    assert res.normalized_payload["customer_name"] == "Arjun"
    assert "name" not in res.normalized_payload


# T28 -- Alias does not inject absent field
def test_t28_alias_does_not_inject_absent_field():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"name": "Arjun", "contact_email": "sales@acme.example"}
    )
    assert "name" not in res.normalized_payload
    assert "customer_name" not in res.normalized_payload


# T29 -- Both aliases explicitly configured
def test_t29_both_aliases_configured():
    fields = [
        {"key": "name", "label": "Name", "type": "string", "required": True},
        {"key": "customer_name", "label": "Customer Name", "type": "string", "required": True},
    ]
    ctx = _make_context(fields=fields)
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"name": "Arjun", "customer_name": "Arjun R"}
    )
    assert res.valid is True
    assert "name" in res.normalized_payload
    assert "customer_name" in res.normalized_payload
    assert res.normalized_payload["name"] == "Arjun"
    assert res.normalized_payload["customer_name"] == "Arjun R"


# T30 -- Input mapping unchanged
def test_t30_input_mapping_unchanged():
    ctx = _make_context()
    payload = {"company_name": "Acme", "contact_email": "sales@acme.example"}
    original = copy.deepcopy(payload)
    lead_capture_module.validate_terminal_configured_payload(ctx, payload)
    assert payload == original


# T31 -- Context state unchanged
def test_t31_context_state_unchanged():
    ctx = _make_context()
    snapshot = copy.deepcopy(ctx.session_data.state)
    lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    assert ctx.session_data.state == snapshot


# T32 -- No tool or registry invocation
def test_t32_no_tool_or_registry_invocation():
    ctx = _make_context()
    with patch("backend.tools.registry.ToolRegistry.execute_tool") as reg_mock, \
         patch.object(lead_capture_module, "execute") as exec_mock:
        lead_capture_module.validate_terminal_configured_payload(
            ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
        )
        assert reg_mock.call_count == 0
        assert exec_mock.call_count == 0


# T33 -- No persistence or event invocation
def test_t33_no_persistence_or_event_invocation():
    ctx = _make_context()
    with patch("backend.domains.leads.tool_handlers.execute_lead_capture_tool") as handler_mock, \
         patch("backend.domains.leads.service.LeadService.capture_lead") as svc_mock, \
         patch("backend.domains.leads.repository.LeadRepository.capture_lead") as repo_mock, \
         patch("backend.domains.events.EventService.emit") as event_mock:
        lead_capture_module.validate_terminal_configured_payload(
            ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
        )
        assert handler_mock.call_count == 0
        assert svc_mock.call_count == 0
        assert repo_mock.call_count == 0
        assert event_mock.call_count == 0


# T34 -- Normalized payload copied
def test_t34_normalized_payload_copied():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    payload = res.normalized_payload
    mutated = dict(payload)
    mutated["company_name"] = "Changed"
    assert res.normalized_payload["company_name"] == "Acme"


# T35 -- Frozen result
def test_t35_frozen_result():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    try:
        res.valid = False
        raised = False
    except Exception:
        raised = True
    assert raised is True


# T36 -- Deterministic reason precedence
def test_t36_reason_precedence():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx,
        {
            "company_name": "Acme",
            "contact_email": "sales@acme.example",
            "tenant_id": "X",
            "marketing_consent": "yes",
            "unknown_field": "y",
        },
    )
    assert res.valid is False
    assert res.reason_code == "protected_fields"


# T37 -- Registry-schema parity
def test_t37_registry_schema_parity():
    ctx = _make_context()
    schema = get_lead_capture_tool_schema(ctx.config.config_data)
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    props = schema["properties"]
    for key in res.normalized_payload:
        assert key in props
    for key in res.required_field_keys:
        assert key in schema["required"]


# T38 -- Tenant-required flags authoritative
def test_t38_tenant_required_flags_authoritative():
    ctx = _make_context()
    res = lead_capture_module.validate_terminal_configured_payload(
        ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
    )
    assert "industry" not in res.required_field_keys


# T39 -- Current validate_payload not called
def test_t39_legacy_validate_payload_not_called():
    ctx = _make_context()
    with patch.object(lead_capture_module, "validate_payload", side_effect=RuntimeError("should not be called")) as vp:
        lead_capture_module.validate_terminal_configured_payload(
            ctx, {"company_name": "Acme", "contact_email": "sales@acme.example"}
        )
        assert vp.call_count == 0


# T40 -- Existing non-terminal validator unchanged
def test_t40_existing_non_terminal_validator_unchanged():
    ctx = _make_context(fields=[
        {"key": "company_name", "label": "Company", "type": "string", "required": True},
        {"key": "contact_email", "label": "Contact Email", "type": "email", "required": True},
        {"key": "industry", "label": "Industry", "type": "string", "required": False},
        {"key": "phone", "label": "Phone", "type": "phone", "required": False},
        {"key": "email", "label": "Email", "type": "email", "required": False},
    ])
    res = lead_capture_module.validate_external_configured_candidates(
        ctx, {"company_name": "Acme"}
    )
    assert res.accepted_field_keys == ("company_name",)
    res2 = lead_capture_module.validate_external_configured_candidates(
        ctx, {"company_name": ""}
    )
    assert res2.accepted_patch == {}
    res3 = lead_capture_module.validate_external_configured_candidates(
        ctx, {"company_name": "A"}
    )
    assert res3.accepted_patch == {}
    res4 = lead_capture_module.validate_external_configured_candidates(
        ctx, {"company_name": {"x": 1}}
    )
    assert "company_name" in res4.rejected_field_keys
    res5 = lead_capture_module.validate_external_configured_candidates(
        ctx, {"company_name": ["a", "b"]}
    )
    assert "company_name" in res5.rejected_field_keys
    res6 = lead_capture_module.validate_external_configured_candidates(
        ctx, {"phone": "+919999999999"}
    )
    assert res6.accepted_field_keys == ("phone",)
    res7 = lead_capture_module.validate_external_configured_candidates(
        ctx, {"phone": "abc"}
    )
    assert "phone" in res7.rejected_field_keys
    res8 = lead_capture_module.validate_external_configured_candidates(
        ctx, {"email": "arjun@acme.com"}
    )
    assert res8.accepted_field_keys == ("email",)
    res9 = lead_capture_module.validate_external_configured_candidates(
        ctx, {"email": "not-an-email"}
    )
    assert "email" in res9.rejected_field_keys
