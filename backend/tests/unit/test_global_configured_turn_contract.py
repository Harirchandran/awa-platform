"""Iteration 2 dynamic configured turn contract tests.

These tests cover the non-authoritative, additive dynamic turn contract in
``backend.logic.engine.field_extraction.extract_global_workflow_turn``.

The centralized gateway (``backend.ai.gateway``) is mocked. No real provider
is contacted and no legacy LLM path is exercised.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from backend.ai.gateway.models import LLMParseStatus, LLMResult
from backend.logic.engine.field_extraction import extract_global_workflow_turn
from backend.logic.engine.field_extraction_gateway import (
    DynamicTurnContractPayload,
    DynamicTurnFieldCandidate,
    DynamicTurnValidationResult,
    build_dynamic_turn_gateway_request,
)


CONFIGURED_FIELDS = [
    {
        "field_key": "visit_date",
        "display_label": "Visit Date",
        "data_type": "date",
        "required": True,
    },
    {
        "field_key": "visit_time",
        "display_label": "Visit Time",
        "data_type": "time",
        "required": True,
    },
    {
        "field_key": "customer_name",
        "display_label": "Name",
        "data_type": "string",
        "required": True,
    },
    {
        "field_key": "phone",
        "display_label": "Phone",
        "data_type": "phone",
        "required": True,
    },
]


def _base_semantic_input(focus: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "customer_text": "Tomorrow evening at four. I'm Arjun and this number is fine.",
        "workflow": "booking",
        "channel_category": "whatsapp",
        "language": "en",
        "tenant_timezone": "Asia/Kolkata",
        "configured_fields": [dict(f) for f in CONFIGURED_FIELDS],
        "collected_field_keys": [],
        "missing_required_field_keys": ["visit_date", "visit_time", "customer_name", "phone"],
    }
    if focus is not None:
        payload["focus_field_key"] = focus
    return payload


def _fake_result(content: Any, parse_status: str = "success", error: Any = None) -> LLMResult:
    return LLMResult(
        content=content,
        provider="gemini",
        model="fake",
        latency_ms=10,
        parse_status=LLMParseStatus(parse_status),
        error=error,
    )


class _FakeGateway:
    def __init__(self, result: LLMResult | Exception):
        self._result = result
        self.invoke = AsyncMock()

    async def _do_invoke(self, request):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    def __call__(self):  # pragma: no cover - helper
        return self


def _make_gateway(result: LLMResult | Exception) -> _FakeGateway:
    gw = _FakeGateway(result)
    gw.invoke = AsyncMock(side_effect=gw._do_invoke)
    return gw


def _candidate(field_key: str, value: Any, evidence: str, confidence: float = 0.9) -> dict[str, Any]:
    return {
        "field_key": field_key,
        "candidate_value": value,
        "confidence": confidence,
        "evidence_text": evidence,
    }


def _payload_dict(candidates, clarification_needed=False, clarification_reason=None):
    data = {"candidates": candidates}
    if clarification_needed:
        data["clarification_needed"] = True
    if clarification_reason:
        data["clarification_reason"] = clarification_reason
    return data


async def _run(semantic_input: dict[str, Any], result: LLMResult | Exception) -> Any:
    gw = _make_gateway(result)
    with patch(
        "backend.ai.gateway.runtime.get_runtime_llm_gateway",
        return_value=gw,
    ):
        outcome = await extract_global_workflow_turn(semantic_input)
    return outcome, gw


# T1 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_exact_configured_keys_are_sent():
    semantic_input = _base_semantic_input()
    outcome, gw = await _run(
        semantic_input,
        _fake_result(_payload_dict([
            _candidate("visit_date", "2026-07-18", "Tomorrow"),
        ])),
    )
    request = gw.invoke.call_args.args[0]
    sent_keys = {f["field_key"] for f in request.messages[1].content and {}}
    # content is json string; parse it
    import json
    sent = json.loads(request.messages[1].content)
    assert {f["field_key"] for f in sent["configured_fields"]} == {
        "visit_date", "visit_time", "customer_name", "phone"
    }


# T2 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_focus_does_not_filter_fields():
    semantic_input = _base_semantic_input(focus="visit_date")
    outcome, gw = await _run(
        semantic_input,
        _fake_result(_payload_dict([
            _candidate("visit_date", "2026-07-18", "Tomorrow"),
        ])),
    )
    import json
    sent = json.loads(gw.invoke.call_args.args[0].messages[1].content)
    assert {f["field_key"] for f in sent["configured_fields"]} == {
        "visit_date", "visit_time", "customer_name", "phone"
    }
    # focus must be present as a hint, not a filter
    assert sent["focus_field_key"] == "visit_date"


# T3 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_complete_turn_multi_field_candidates_survive():
    semantic_input = _base_semantic_input()
    outcome, gw = await _run(
        semantic_input,
        _fake_result(_payload_dict([
            _candidate("visit_date", "2026-07-18", "Tomorrow"),
            _candidate("visit_time", "16:00", "at four", confidence=0.85),
            _candidate("customer_name", "Arjun", "I'm Arjun"),
            _candidate("phone", "+919999999999", "this number is fine"),
        ])),
    )
    assert isinstance(outcome, DynamicTurnValidationResult)
    assert not outcome.rejected
    keys = {c.field_key for c in outcome.accepted_candidates}
    assert keys == {"visit_date", "visit_time", "customer_name", "phone"}


# T4 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_arbitrary_tenant_keys_accepted_when_configured():
    configured = [
        {"field_key": k, "display_label": k, "data_type": "string", "required": False}
        for k in [
            "insurance_policy_number", "pet_breed",
            "preferred_contact_window", "vehicle_registration", "consultation_budget",
        ]
    ]
    semantic_input = {
        "customer_text": "My policy is P123, breed is labrador.",
        "workflow": "booking",
        "channel_category": "web",
        "language": "en",
        "tenant_timezone": "UTC",
        "configured_fields": configured,
        "collected_field_keys": [],
        "missing_required_field_keys": [],
    }
    outcome, gw = await _run(
        semantic_input,
        _fake_result(_payload_dict([
            _candidate("insurance_policy_number", "P123", "policy is P123"),
            _candidate("pet_breed", "labrador", "breed is labrador"),
            _candidate("preferred_contact_window", "morning", "prefer morning"),
            _candidate("vehicle_registration", "KA01XX1234", "vehicle KA01XX1234"),
            _candidate("consultation_budget", "5000", "budget 5000"),
        ])),
    )
    assert not outcome.rejected
    assert len(outcome.accepted_candidates) == 5


# T5 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_arbitrary_keys_rejected_when_not_configured():
    semantic_input = _base_semantic_input()
    outcome, gw = await _run(
        semantic_input,
        _fake_result(_payload_dict([
            _candidate("pet_breed", "labrador", "breed is labrador"),
        ])),
    )
    assert outcome.rejected
    assert outcome.gateway_unavailable is False
    assert len(outcome.accepted_candidates) == 0


# T6 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_keys_rejected():
    semantic_input = _base_semantic_input()
    dup = _candidate("visit_date", "2026-07-18", "Tomorrow")
    outcome, gw = await _run(
        semantic_input,
        _fake_result(_payload_dict([dup, dup])),
    )
    assert outcome.rejected
    assert len(outcome.accepted_candidates) == 0


# T7 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_extra_properties_rejected():
    semantic_input = _base_semantic_input()
    # extra property on top-level payload
    bad_top = _payload_dict([_candidate("visit_date", "2026-07-18", "Tomorrow")])
    bad_top["unexpected_top"] = "x"
    outcome, gw = await _run(semantic_input, _fake_result(bad_top))
    assert outcome.rejected
    assert len(outcome.accepted_candidates) == 0

    # extra property on a candidate
    bad_cand = _payload_dict([
        {**_candidate("visit_date", "2026-07-18", "Tomorrow"), "extra_field": "y"}
    ])
    outcome2, _ = await _run(semantic_input, _fake_result(bad_cand))
    assert outcome2.rejected
    assert len(outcome2.accepted_candidates) == 0


# T8 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_invalid_confidence_rejected():
    semantic_input = _base_semantic_input()
    for bad_conf in (-0.1, 1.1):
        outcome, _ = await _run(
            semantic_input,
            _fake_result(_payload_dict([
                _candidate("visit_date", "2026-07-18", "Tomorrow", confidence=bad_conf),
            ])),
        )
        assert outcome.rejected
        assert len(outcome.accepted_candidates) == 0


# T9 ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unsupported_nested_object_rejected():
    semantic_input = _base_semantic_input()
    outcome, _ = await _run(
        semantic_input,
        _fake_result(_payload_dict([
            {
                "field_key": "visit_date",
                "candidate_value": {"unexpected": "object"},
                "confidence": 0.9,
                "evidence_text": "nope",
            }
        ])),
    )
    assert outcome.rejected
    assert len(outcome.accepted_candidates) == 0


# T10 -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_candidates_valid():
    semantic_input = _base_semantic_input()
    outcome, _ = await _run(
        semantic_input,
        _fake_result(_payload_dict(
            [], clarification_needed=True, clarification_reason="ambiguous time"
        )),
    )
    assert not outcome.rejected
    assert outcome.accepted_candidates == []
    assert outcome.clarification_needed is True


# T11 -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_gateway_failure_is_fail_closed():
    semantic_input = _base_semantic_input()
    # provider error
    outcome, _ = await _run(semantic_input, RuntimeError("boom"))
    assert outcome.rejected
    assert outcome.gateway_unavailable is True

    # parse error (failed status)
    outcome2, _ = await _run(
        semantic_input,
        _fake_result({}, parse_status="failed"),
    )
    assert outcome2.rejected
    assert outcome2.gateway_unavailable is True

    # unavailable / None content
    outcome3, _ = await _run(
        semantic_input,
        _fake_result(None, parse_status="success"),
    )
    assert outcome3.rejected
    assert outcome3.gateway_unavailable is True

    # assert no exception escapes: all branches above returned cleanly
    assert isinstance(outcome, DynamicTurnValidationResult)


# T12 -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_input_objects_are_unchanged():
    semantic_input = _base_semantic_input(focus="visit_date")
    import copy
    snapshot = copy.deepcopy(semantic_input)
    await _run(
        semantic_input,
        _fake_result(_payload_dict([
            _candidate("visit_date", "2026-07-18", "Tomorrow"),
        ])),
    )
    assert semantic_input == snapshot


# T13 -----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_execution_claims_allowed():
    semantic_input = _base_semantic_input()
    forbidden = _payload_dict([
        {
            "field_key": "visit_date",
            "candidate_value": "2026-07-18",
            "confidence": 0.9,
            "evidence_text": "Tomorrow",
            "booking_id": "B123",
            "tool_name": "create_booking",
            "execution_success": True,
            "customer_response": "Booked!",
        }
    ])
    outcome, _ = await _run(semantic_input, _fake_result(forbidden))
    assert outcome.rejected
    assert len(outcome.accepted_candidates) == 0
