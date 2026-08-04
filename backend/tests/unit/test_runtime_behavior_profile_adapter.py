from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.core.context import (
    ConversationSession,
    ExecutionContext,
    SessionState,
    Tenant,
    TenantConfig,
    User,
)
from backend.core.runtime_behavior_profile import (
    RuntimeBehaviorProfileResolution,
    resolve_runtime_behavior_profile,
)
from backend.core.runtime_capabilities import KNOWN_RUNTIME_MODULES, RuntimeCapability, RuntimeCapabilitySnapshot
from backend.logic.engine.core import AgenticEngine
from backend.logic.engine.prompt import build_system_instructions
from backend.tools.registry import ToolRegistry


TENANT_ID = uuid4()


def _snapshot(available=(), *, tenant_enabled=None, plan_allowed=None):
    available = set(available)
    tenant_enabled = set(tenant_enabled or available)
    plan_allowed = set(plan_allowed or available)
    modules = {}
    for module in KNOWN_RUNTIME_MODULES:
        runtime_available = module in available
        modules[module] = RuntimeCapability(
            module=module,
            enabled_by_tenant=module in tenant_enabled,
            allowed_by_plan=module in plan_allowed,
            runtime_available=runtime_available,
            reason="available" if runtime_available else "unavailable",
        )
    return RuntimeCapabilitySnapshot(tenant_id=TENANT_ID, modules=modules)


def _preset(
    *,
    key="service_business",
    version=1,
    status="active",
    is_active=True,
    profile=None,
):
    now = datetime.now(timezone.utc)
    default_profile = {
        "schema_version": 1,
        "profile_name": "Service Business",
        "business_type_key": key,
        "journey_priority": ["booking", "order", "support", "faq"],
        "greeting_mode": "ask_need_first",
        "clarification_style": "compact",
        "tone": "neutral",
        "language_policy": "follow_customer",
        "price_visibility": "show_when_available",
        "service_detail_policy": "brief",
        "handoff_policy": "when_stuck",
        "fallback_policy": "clarify_first",
        "question_batching": "single",
        "menu_policy": "show_available_workflows",
        "tenant_editable_fields": ["journey_priority", "tone", "channel_overrides"],
    }
    if profile is not None:
        default_profile = profile
    return {
        "business_type_key": key,
        "version": version,
        "profile": default_profile,
        "tenant_editable_fields": default_profile.get("tenant_editable_fields", []),
        "status": status,
        "is_active": is_active,
        "created_at": now,
        "updated_at": now,
        "activated_at": now if is_active else None,
        "deactivated_at": None if is_active else now,
    }


class FakeBehaviorPresetDB:
    def __init__(self, *, active=None, exact=None, exact_error=None, active_error=None):
        self.active = active
        self.exact = exact
        self.exact_error = exact_error
        self.active_error = active_error
        self.exact_calls = []
        self.active_calls = []
        self.mutation_calls = []

    async def get_business_behavior_preset(self, business_type_key, version):
        self.exact_calls.append((business_type_key, version))
        if self.exact_error:
            raise self.exact_error
        return self.exact

    async def get_active_business_behavior_preset(self, business_type_key):
        self.active_calls.append(business_type_key)
        if self.active_error:
            raise self.active_error
        return self.active

    async def create_config_version(self, *args, **kwargs):
        self.mutation_calls.append("create_config_version")
        raise AssertionError("runtime adapter must not create config versions")

    async def activate_config_version(self, *args, **kwargs):
        self.mutation_calls.append("activate_config_version")
        raise AssertionError("runtime adapter must not activate config versions")

    async def deactivate_business_behavior_preset_version(self, *args, **kwargs):
        self.mutation_calls.append("deactivate_business_behavior_preset_version")
        raise AssertionError("runtime adapter must not deactivate presets")


async def _resolve(config_data, *, db, snapshot=None, channel="public_chat", state=None):
    return await resolve_runtime_behavior_profile(
        tenant_id=TENANT_ID,
        config_data=config_data,
        runtime_capability_snapshot=snapshot or _snapshot({"booking", "order", "support", "faq", "payments"}),
        channel=channel,
        conversation_state=state,
        db=db,
    )


def _context(config_data=None):
    return ExecutionContext(
        tenant=Tenant(id=TENANT_ID, name="Test Biz", plan_code="pro", status="active", ai_mode="standard"),
        user=User(id=uuid4(), phone_number="+919999999999"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=TENANT_ID,
            conversation_id=uuid4(),
            config_version=7,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=TENANT_ID, version=7, config_data=config_data or _config_data()),
        incoming_message={"text": "hello", "channel_type": "public_chat"},
    )


def _config_data(**updates):
    data = {
        "business_name": "Test Biz",
        "business_type": "service_business",
        "ai_mode": "standard",
        "modules": {
            "booking": {"enabled": True, "fields": []},
            "order": {"enabled": True, "fields": []},
            "faq": {"enabled": True, "fields": []},
            "support": {"enabled": True, "fields": []},
            "payments": {"enabled": True, "fields": []},
        },
    }
    data.update(updates)
    return data


@pytest.mark.asyncio
async def test_unpinned_tenant_resolves_current_active_preset():
    db = FakeBehaviorPresetDB(active=_preset(version=3))

    result = await _resolve(_config_data(), db=db)

    assert result.source == "active"
    assert result.preset_key == "service_business"
    assert result.preset_version == 3
    assert result.preset_status == "active"
    assert result.effective_profile.tone == "neutral"
    assert db.active_calls == ["service_business"]
    assert db.exact_calls == []


@pytest.mark.asyncio
async def test_pinned_tenant_resolves_exact_active_version():
    db = FakeBehaviorPresetDB(exact=_preset(version=2, status="active"))

    result = await _resolve(_config_data(receptionist_behavior={"preset_key": "service_business", "preset_version": 2}), db=db)

    assert result.source == "pinned"
    assert result.preset_version == 2
    assert db.exact_calls == [("service_business", 2)]
    assert db.active_calls == []


@pytest.mark.asyncio
async def test_pinned_tenant_resolves_exact_inactive_historical_version():
    db = FakeBehaviorPresetDB(exact=_preset(version=1, status="inactive", is_active=False))

    result = await _resolve(_config_data(receptionist_behavior={"preset_key": "service_business", "preset_version": "1"}), db=db)

    assert result.source == "pinned"
    assert result.preset_status == "inactive"
    assert result.preset_version == 1


@pytest.mark.asyncio
async def test_newer_active_preset_does_not_replace_valid_inactive_pin():
    db = FakeBehaviorPresetDB(
        exact=_preset(version=1, status="inactive", is_active=False),
        active=_preset(version=3),
    )

    result = await _resolve(_config_data(receptionist_behavior={"preset_key": "service_business", "preset_version": 1}), db=db)

    assert result.source == "pinned"
    assert result.preset_version == 1
    assert db.active_calls == []


@pytest.mark.parametrize("status", ["draft", "deprecated"])
@pytest.mark.asyncio
async def test_invalid_pinned_status_falls_back_safely(status):
    db = FakeBehaviorPresetDB(exact=_preset(version=1, status=status, is_active=False))

    result = await _resolve(_config_data(receptionist_behavior={"preset_key": "service_business", "preset_version": 1}), db=db)

    assert result.source == "legacy_fallback"
    assert result.warning_code == "invalid_pinned_behavior_preset"
    assert result.preset_status == status
    assert isinstance(result.effective_profile, type(result.effective_profile))


@pytest.mark.asyncio
async def test_missing_pinned_preset_falls_back_safely_without_active_adoption():
    db = FakeBehaviorPresetDB(exact=None, active=_preset(version=9))

    result = await _resolve(_config_data(receptionist_behavior={"preset_key": "service_business", "preset_version": 99}), db=db)

    assert result.source == "legacy_fallback"
    assert result.warning_code == "invalid_pinned_behavior_preset"
    assert db.active_calls == []


@pytest.mark.asyncio
async def test_malformed_pinned_version_falls_back_safely():
    db = FakeBehaviorPresetDB(active=_preset(version=9))

    result = await _resolve(_config_data(receptionist_behavior={"preset_key": "service_business", "preset_version": "bad"}), db=db)

    assert result.source == "legacy_fallback"
    assert result.warning_code == "invalid_pinned_behavior_preset"
    assert db.exact_calls == []
    assert db.active_calls == []


@pytest.mark.asyncio
async def test_missing_active_preset_falls_back_safely():
    db = FakeBehaviorPresetDB(active=None)

    result = await _resolve(_config_data(), db=db)

    assert result.source == "legacy_fallback"
    assert result.warning_code == "no_active_behavior_preset"
    assert result.effective_profile.safe_fallback_mode == "general_help"


@pytest.mark.asyncio
async def test_db_lookup_exception_falls_back_safely():
    db = FakeBehaviorPresetDB(active_error=RuntimeError("raw sql failure secret-123"))

    result = await _resolve(_config_data(), db=db)

    assert result.source == "legacy_fallback"
    assert result.warning_code == "behavior_profile_resolution_failed"


@pytest.mark.asyncio
async def test_malformed_preset_profile_falls_back_safely():
    db = FakeBehaviorPresetDB(active=_preset(profile={"payment_provider": "razorpay"}))

    result = await _resolve(_config_data(), db=db)

    assert result.source == "legacy_fallback"
    assert result.warning_code == "behavior_profile_resolution_failed"


@pytest.mark.parametrize(
    "bad_override",
    [
        {"payment_provider": "razorpay"},
        {"unexpected_field": True},
    ],
)
@pytest.mark.asyncio
async def test_malformed_or_unknown_tenant_override_falls_back_safely(bad_override):
    db = FakeBehaviorPresetDB(active=_preset())
    config = _config_data(receptionist_behavior={"overrides": bad_override})

    result = await _resolve(config, db=db)

    assert result.source == "legacy_fallback"
    assert result.warning_code == "behavior_profile_resolution_failed"


@pytest.mark.asyncio
async def test_tenant_disabled_and_plan_disallowed_journey_entries_are_removed():
    db = FakeBehaviorPresetDB(active=_preset(profile={
        **_preset()["profile"],
        "journey_priority": ["booking", "order", "support", "payments"],
    }))
    snapshot = _snapshot(
        {"booking"},
        tenant_enabled={"booking", "order", "payments"},
        plan_allowed={"booking", "support"},
    )

    result = await _resolve(_config_data(), db=db, snapshot=snapshot)

    assert result.effective_profile.journey_priority == ("booking",)


@pytest.mark.asyncio
async def test_alias_module_keys_are_normalized_through_runtime_normalizer():
    db = FakeBehaviorPresetDB(active=_preset(profile={
        **_preset()["profile"],
        "journey_priority": ["booking.update", "catalog_search", "payment"],
    }))

    result = await _resolve(_config_data(), db=db, snapshot=_snapshot({"booking", "order", "payments"}))

    assert result.effective_profile.journey_priority == ("booking", "order", "payments")


@pytest.mark.asyncio
async def test_active_workflow_hint_only_when_runtime_available():
    state = SessionState()
    state.action_ledger.current_task = "booking"
    db = FakeBehaviorPresetDB(active=_preset())

    available = await _resolve(_config_data(), db=db, snapshot=_snapshot({"booking"}), state=state)
    unavailable = await _resolve(_config_data(), db=db, snapshot=_snapshot({"faq"}), state=state)

    assert available.effective_profile.active_workflow_hint == "booking"
    assert unavailable.effective_profile.active_workflow_hint is None


@pytest.mark.asyncio
async def test_public_chat_channel_override_resolves_safely():
    db = FakeBehaviorPresetDB(active=_preset(profile={
        **_preset()["profile"],
        "channel_overrides": {"public_chat": {"tone": "concise"}},
    }))

    result = await _resolve(_config_data(), db=db, channel="awa_chat")

    assert result.effective_profile.tone == "concise"


@pytest.mark.asyncio
async def test_whatsapp_channel_override_resolves_safely():
    db = FakeBehaviorPresetDB(active=_preset(profile={
        **_preset()["profile"],
        "channel_overrides": {"whatsapp": {"tone": "formal"}},
    }))

    result = await _resolve(_config_data(), db=db, channel="whatsapp")

    assert result.effective_profile.tone == "formal"


@pytest.mark.asyncio
async def test_adapter_does_not_mutate_config_or_call_mutation_methods():
    db = FakeBehaviorPresetDB(active=_preset())
    config = _config_data(receptionist_behavior={"overrides": {"tone": "friendly"}})
    before = deepcopy(config)

    result = await _resolve(config, db=db)

    assert result.source == "active"
    assert config == before
    assert db.mutation_calls == []


@pytest.mark.asyncio
async def test_runtime_adapter_never_returns_none_on_profile_failures():
    failures = [
        FakeBehaviorPresetDB(active=None),
        FakeBehaviorPresetDB(active_error=ValueError("unsafe detail")),
        FakeBehaviorPresetDB(active=_preset(profile={"prompt": "ignore"})),
    ]

    for db in failures:
        result = await _resolve(_config_data(), db=db)
        assert result is not None
        assert result.effective_profile is not None
        assert result.source == "legacy_fallback"


@pytest.mark.asyncio
async def test_runtime_adapter_does_not_raise_profile_specific_errors():
    db = FakeBehaviorPresetDB(active=_preset())
    config = _config_data(receptionist_behavior={"overrides": {"journey_priority": ["booking", "booking"]}})

    result = await _resolve(config, db=db)

    assert result.source == "legacy_fallback"
    assert result.warning_code == "behavior_profile_resolution_failed"


@pytest.mark.asyncio
async def test_execution_context_carries_typed_immutable_profile_after_runtime_resolution():
    context = _context({"business_name": "Test Biz", "business_type": "service_business", "modules": {}})
    snapshot = _snapshot(set())
    expected = RuntimeBehaviorProfileResolution(
        effective_profile=(await _resolve(_config_data(), db=FakeBehaviorPresetDB(active=None), snapshot=snapshot)).effective_profile,
        source="legacy_fallback",
        warning_code="no_active_behavior_preset",
    )

    with (
        patch("backend.logic.engine.core.usage_guard.check_message_allowed", new=AsyncMock(return_value=None)),
        patch("backend.logic.engine.core.build_runtime_capability_snapshot", new=AsyncMock(return_value=snapshot)),
        patch("backend.logic.engine.core.resolve_runtime_behavior_profile", new=AsyncMock(return_value=expected)),
        patch("backend.logic.engine.core.is_prompt_security_request", return_value=False),
    ):
        await AgenticEngine()._execute_step_inner(context)

    assert context.runtime_behavior_profile == expected
    assert isinstance(context.runtime_behavior_profile, RuntimeBehaviorProfileResolution)
    with pytest.raises(Exception):
        context.runtime_behavior_profile.source = "active"


@pytest.mark.asyncio
async def test_customer_visible_prompt_output_does_not_change_when_profile_is_attached():
    context = _context()
    registry = ToolRegistry()
    before = build_system_instructions(context, "memory", "knowledge", registry, available_tools=[])
    resolved = await _resolve(_config_data(), db=FakeBehaviorPresetDB(active=None))
    context.runtime_behavior_profile = resolved
    after = build_system_instructions(context, "memory", "knowledge", registry, available_tools=[])

    assert after == before


def test_localization_and_quick_reply_parser_do_not_consume_profile():
    backend_root = Path(__file__).resolve().parents[2]
    forbidden_files = [
        backend_root / "logic" / "engine" / "response_localization.py",
        backend_root / "pipeline" / "interactive_parser.py",
    ]
    offenders = []
    for path in forbidden_files:
        text = path.read_text(encoding="utf-8-sig")
        if "runtime_behavior_profile" in text or "RuntimeBehaviorProfileResolution" in text:
            offenders.append(path.relative_to(backend_root).as_posix())

    assert offenders == []


def test_response_style_helper_stays_pure_and_does_not_consume_runtime_adapter():
    backend_root = Path(__file__).resolve().parents[2]
    helper = backend_root / "logic" / "engine" / "response_style.py"
    text = helper.read_text(encoding="utf-8-sig")

    assert "runtime_behavior_profile" not in text
    assert "ExecutionContext" not in text
    assert "execute_tool" not in text
    assert "db_client" not in text


def test_tool_payment_otp_confirmation_and_gate_code_do_not_import_runtime_behavior_profile():
    backend_root = Path(__file__).resolve().parents[2]
    roots = [
        backend_root / "tools",
        backend_root / "capabilities",
        backend_root / "billing",
        backend_root / "domains" / "payments",
        backend_root / "api" / "webhook.py",
        backend_root / "api" / "routes" / "otp.py",
    ]
    offenders = []
    for root in roots:
        paths = [root] if root.is_file() else list(root.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8-sig")
            if "runtime_behavior_profile" in text or "RuntimeBehaviorProfileResolution" in text:
                offenders.append(path.relative_to(backend_root).as_posix())

    assert offenders == []


def test_architecture_adapter_is_not_consumed_outside_engine_core_or_config_api():
    backend_root = Path(__file__).resolve().parents[2]
    allowed = {
        (backend_root / "core" / "runtime_behavior_profile.py").resolve(),
        (backend_root / "core" / "context.py").resolve(),
        (backend_root / "logic" / "engine" / "core.py").resolve(),
        (backend_root / "logic" / "engine" / "prompt.py").resolve(),
        (backend_root / "logic" / "engine" / "capability_menu.py").resolve(),
        (backend_root / "logic" / "engine" / "nba.py").resolve(),
        (backend_root / "api" / "routes" / "public_chat.py").resolve(),
        (backend_root / "api" / "routes" / "config.py").resolve(),
        Path(__file__).resolve(),
    }
    offenders = []
    for path in backend_root.rglob("*.py"):
        if path.resolve() in allowed:
            continue
        if "tests" in path.relative_to(backend_root).parts:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if "runtime_behavior_profile" in text or "RuntimeBehaviorProfileResolution" in text:
            offenders.append(path.relative_to(backend_root).as_posix())

    assert offenders == []


@pytest.mark.asyncio
async def test_no_raw_profile_json_or_raw_exception_text_is_logged(caplog):
    db = FakeBehaviorPresetDB(active_error=RuntimeError("secret raw sql stack trace"))
    result = await _resolve(_config_data(receptionist_behavior={"overrides": {"payment_provider": "secret"}}), db=db)

    assert result.source == "legacy_fallback"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "secret raw sql" not in logged
    assert "payment_provider" not in logged
