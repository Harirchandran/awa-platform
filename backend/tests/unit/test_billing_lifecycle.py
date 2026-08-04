from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from backend.api.routes.subscriptions import compute_billing_lifecycle_status
from backend.api.webhook import _razorpay_event_id
from backend.billing.usage_guard import UsageGuard, UsageLimitExceeded
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
pytestmark = pytest.mark.anyio
class EmptyUsageDb:
    async def get_tenant_usage_summary(self, tenant_id):
        return {
            "today_message_count": 0,
            "month_message_count": 0,
            "month_ai_tokens_used": 0,
            "month_credits_used": 0,
            "month_tool_action_count": 0,
        }
    async def get_subscription_plan_by_code(self, plan_code):
        return None
def make_context(status="active"):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Billing Clinic", plan_code="pro", status=status),
        user=User(id=uuid4(), phone_number="+15550001111"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"modules": {}}),
        incoming_message={"text": "hi", "channel_type": "awa_chat"},
    )
def test_billing_lifecycle_marks_active_renewal_overdue():
    lifecycle = compute_billing_lifecycle_status({
        "status": "active",
        "razorpay_subscription_id": "sub_123",
        "current_period_end": datetime.now(timezone.utc) - timedelta(days=1),
    })
    assert lifecycle["lifecycle_status"] == "renewal_overdue"
    assert lifecycle["attention_required"] is True
    assert lifecycle["saas_billing_only"] is True
def test_billing_lifecycle_marks_suspended_subscription_as_past_due_or_suspended():
    lifecycle = compute_billing_lifecycle_status({
        "status": "suspended",
        "razorpay_subscription_id": "sub_123",
    })
    assert lifecycle["lifecycle_status"] == "past_due_or_suspended"
    assert lifecycle["attention_required"] is True
def test_razorpay_event_id_uses_provider_event_id_or_stable_fallback():
    payload = {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_123",
                    "subscription_id": "sub_123",
                    "created_at": 123456,
                }
            }
        },
    }
    assert _razorpay_event_id({"id": "evt_123"}) == "evt_123"
    assert _razorpay_event_id(payload) == _razorpay_event_id(dict(payload))
    assert _razorpay_event_id(payload).startswith("derived:")
async def test_usage_guard_blocks_past_due_and_cancelled_statuses():
    guard = UsageGuard(EmptyUsageDb())
    for status in ("past_due", "cancelled", "suspended", "expired"):
        with pytest.raises(UsageLimitExceeded) as exc:
            await guard.check_message_allowed(make_context(status=status))
        assert exc.value.reason == f"tenant_status:{status}"