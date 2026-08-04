import pytest
from backend.db.client import db_client
from backend.api.rate_limiter import limiter
# Disable rate limiting globally during test executions to avoid connecting to Redis
limiter.enabled = False


_RUNTIME_FILES = {
    "test_customer_turn_runtime.py",
    "test_new_modules.py",
    "test_awa_chat_contract.py",
    "test_booking_workflow_recovery.py",
    "test_channel_output_normalization.py",
    "test_intent_switching.py",
    "test_prompt_validation.py",
    "test_session_postgres.py",
    "test_structured_response_envelope.py",
    "test_unified_agent_regression_suite.py",
    "test_whatsapp_interactive_contract.py",
    "test_worker_state_overwrite.py",
    "test_workflow_state_preservation.py",
}

_CHANNEL_FILES = {
    "test_awa_chat_contract.py",
    "test_channel_output_normalization.py",
    "test_customer_turn_runtime.py",
    "test_whatsapp_interactive_contract.py",
}

_CANONICAL_TRANSCRIPT_FILES = {
    "test_awa_chat_contract.py",
    "test_booking_workflow_recovery.py",
    "test_channel_output_normalization.py",
    "test_conversation_context_consistency.py",
    "test_customer_turn_runtime.py",
    "test_lead_support_workflow_recovery.py",
    "test_next_best_action.py",
    "test_order_workflow_recovery.py",
    "test_payment_workflow_recovery.py",
    "test_receptionist_response_layer.py",
    "test_reminder_review_workflow_recovery.py",
    "test_webhook_idempotency_flow.py",
    "test_whatsapp_interactive_contract.py",
}

_CRITICAL_SAFETY_FILES = {
    "test_ai_security.py",
    "test_capability_gateway.py",
    "test_cross_tenant_isolation_api.py",
    "test_otp_data_migration.py",
    "test_runtime_capability_snapshot.py",
    "test_tenant_admin_entitlement_guards.py",
    "test_tenant_config_limits_ignored.py",
    "test_tool_registry_usage_guard.py",
    "test_ungated_tool_enforcement.py",
    "test_webhook_idempotency_flow.py",
    "test_workflow_config_awareness_booking.py",
    "test_workflow_config_awareness_order_payment.py",
}


def pytest_collection_modifyitems(items):
    """Classify established suites without changing their assertions or execution."""
    for item in items:
        filename = item.path.name
        if filename in _RUNTIME_FILES:
            item.add_marker(pytest.mark.runtime)
            item.add_marker(pytest.mark.customer_turn)
        if filename in _CHANNEL_FILES:
            item.add_marker(pytest.mark.channel)
        if filename in _CANONICAL_TRANSCRIPT_FILES:
            item.add_marker(pytest.mark.canonical_transcript)
        if filename in _CRITICAL_SAFETY_FILES:
            item.add_marker(pytest.mark.critical_safety)
        if "tests/smoke/" in item.nodeid or "tests/live/" in item.nodeid:
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.slow)
        if item.nodeid in {
            "backend/tests/test_phase17b_provider_rate_limiter.py::test_wait_timeout_returns_safe_fallback",
            "backend/tests/test_phase17b_provider_rate_limiter.py::test_observed_langchain_llm_enforces_limits",
            "backend/tests/test_phase17e_provider_limiter_clock_tuning.py::test_wait_timeout_skips_provider_call",
        }:
            item.add_marker(pytest.mark.slow)
@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
@pytest.fixture(autouse=True)
def reset_db_pool_between_tests():
    from backend.api.app import app
    app.dependency_overrides.clear()
    db_client._pool = None

    # Reset and recreate Redis clients to avoid event loop closed/mismatch errors
    from backend.pipeline.session_manager import session_manager
    from backend.pipeline.burst_buffer import burst_buffer
    from backend.observability import provider_rate_limiter
    from backend.realtime import public_chat_events
    import redis.asyncio as aioredis
    from backend.core.config import settings

    provider_rate_limiter._redis_client = None
    public_chat_events._redis_client = None
    session_manager.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    burst_buffer.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    yield

    app.dependency_overrides.clear()
    db_client._pool = None
    provider_rate_limiter._redis_client = None
    public_chat_events._redis_client = None
    session_manager.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    burst_buffer.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
