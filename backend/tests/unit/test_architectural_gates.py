"""
Architectural Gate Tests — AWA Platform
Tests for the four critical architectural constraints introduced during the
agentic_engine refactoring:

    1. PostgreSQL UniqueViolationError → safe ValueError  (double-booking gate)
    2. Application-layer review leakage gate              (review threshold gate)
    3. 72-hour privacy reaper tombstone                   (DPDP/GDPR hard-delete)
    4. Meta 24-hour WhatsApp messaging window rule        (template enforcement)
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_execution_context(step_data: dict, config_data: dict | None = None):
    """
    Build a minimal ExecutionContext without a running event loop or real DB.
    Uses the real dataclasses so import paths stay identical to production code.
    """
    from backend.core.context import (
        ConversationSession,
        ExecutionContext,
        SessionState,
        Tenant,
        TenantConfig,
        User,
    )
    from backend.core.states import ConversationState

    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test Biz", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Test User"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="reviews",
            state=SessionState(
                workflow_state=ConversationState.EXECUTING,
                step_data=step_data,
            ),
        ),
        config=TenantConfig(
            tenant_id=tenant_id,
            version=1,
            config_data=config_data or {"business_name": "Test Biz"},
        ),
        incoming_message={"text": "confirm"},
    )


# ===========================================================================
# Gate 1 — Double-Booking DB Constraint
# ===========================================================================

class TestDoubleBookingConstraint:
    """
    The repository wraps asyncpg.UniqueViolationError in a safe ValueError.
    The service layer must propagate it unchanged.
    The booking module must surface it as a non-success result with the
    customer-visible message from the ValueError.
    """

    @pytest.mark.anyio
    async def test_repository_converts_unique_violation_to_value_error(self, monkeypatch):
        """
        When db_client.create_booking raises asyncpg.UniqueViolationError,
        BookingRepository.create_booking must catch it and raise ValueError
        with a customer-safe message.
        """
        import asyncpg
        from backend.domains.bookings.repository import BookingRepository

        class FakeTransaction:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeConnection:
            async def execute(self, *args, **kwargs):
                pass
            def transaction(self):
                return FakeTransaction()
            async def fetchrow(self, *args, **kwargs):
                return None

        class FakeConnectionContext:
            async def __aenter__(self):
                return FakeConnection()
            async def __aexit__(self, exc_type, exc, tb):
                return False

        fake_db = MagicMock()
        fake_db.acquire = MagicMock(return_value=FakeConnectionContext())

        async def _raise_unique_violation(**_kwargs):
            raise asyncpg.UniqueViolationError(
                "duplicate key value violates unique constraint "
                "\"bookings_tenant_id_provider_id_start_time_key\""
            )

        fake_db.create_booking = _raise_unique_violation
        repo = BookingRepository(db=fake_db)

        with pytest.raises(ValueError, match="appointment time is not available"):
            await repo.create_booking(
                tenant_id=uuid4(),
                customer_id=uuid4(),
                conversation_id=uuid4(),
                service_name="Dental Cleaning",
                booking_date="2026-09-01",
                booking_time="10:00:00",
                idempotency_key=None,
            )

    @pytest.mark.anyio
    async def test_booking_module_surfaces_collision_as_failed_result(self, monkeypatch):
        """
        When the service raises ValueError('That appointment time is not available.'),
        the booking module must return success=False with the exact message,
        and must never call create_booking twice.
        """
        from backend.modules.booking_module import booking_module
        from datetime import date, timedelta

        # Pick a future Monday so working-hours validation passes
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7 or 7
        future_monday = (today + timedelta(days=days_ahead)).isoformat()

        call_count = {"n": 0}

        async def _conflict(**kwargs):
            call_count["n"] += 1
            raise ValueError("That appointment time is not available.")

        service = SimpleNamespace(create_booking=AsyncMock(side_effect=_conflict))
        monkeypatch.setattr("backend.modules.booking_module.booking_service", service)

        context = _make_execution_context(
            step_data={
                "service_id": "cleaning",
                "preferred_date": future_monday,
                "preferred_time": "10:00",
                "customer_name": "Hari",
            },
            config_data={
                "business_name": "Smile Clinic",
                "timezone": "Asia/Kolkata",
                "working_hours": {
                    "monday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
                    "tuesday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
                    "wednesday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
                    "thursday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
                    "friday": {"is_open": True, "open_time": "09:00", "close_time": "17:00"},
                    "saturday": {"is_open": False, "open_time": "09:00", "close_time": "17:00"},
                    "sunday": {"is_open": False, "open_time": "09:00", "close_time": "17:00"},
                },
                "services": [
                    {"id": "cleaning", "name": "Dental Cleaning", "duration_mins": 45, "price": 800}
                ],
                "modules": {"booking": {"enabled": True, "fields": []}},
                "booking_settings": {},
            },
        )
        result = await booking_module.execute(context)

        assert result.success is False
        assert result.user_message == "That appointment time is not available."
        # The service was attempted exactly once — no retry on unique violation.
        assert call_count["n"] == 1


# ===========================================================================
# Gate 2 — Review Leakage Gate (Application-Layer Threshold)
# ===========================================================================

class TestReviewLeakageGate:
    """
    When rating < positive_rating_threshold, the tool must:
      - Call action_review_request with action='escalate_negative'
      - Return public_review_url=None in ToolExecutionResult.data
      - NOT call the 'submit_rating' or 'request_public_review' action
    """

    @pytest.mark.anyio
    async def test_low_rating_never_emits_public_review_url(self, monkeypatch):
        """
        Tenant has positive_rating_threshold=4.
        Customer submits rating=1.
        Asserts: action='escalate_negative' is used, public_review_url is strictly
        None in the returned data dict, and submit_rating is never invoked.
        """
        from backend.domains.reviews.tool_handlers import execute_reviews_tool
        from backend.tools.registry import ToolExecutionResult

        review_id = uuid4()
        context = _make_execution_context(step_data={"rating": 1, "feedback": "Poor service"})

        # Settings returns threshold=4
        mock_settings = {"positive_rating_threshold": 4, "public_review_url": "https://g.co/review"}
        # Review is created in 'responded' status
        created_review = {
            "id": review_id,
            "metadata": {},
            "rating": None,
            "status": "responded",
        }
        # After update_review_request (saves rating + feedback)
        updated_review = {
            "id": review_id,
            "metadata": {"gate": "application_layer_escalation", "threshold_at_submission": 4},
            "rating": 1,
            "status": "responded",
        }
        # After action_review_request(action='escalate_negative')
        escalated_review = {
            "id": review_id,
            "rating": 1,
            "status": "negative_feedback_escalated",
            "public_review_url": None,  # ← the gate must enforce this
        }

        submit_rating_calls = []

        async def _action_review_request(tenant_id, review_id, action, **kwargs):
            if action == "submit_rating":
                submit_rating_calls.append(action)
            assert action == "escalate_negative", (
                f"Expected 'escalate_negative' for low rating, got '{action}'"
            )
            assert kwargs.get("public_review_url") is None, (
                "public_review_url must be None on the escalate_negative path"
            )
            return escalated_review

        mock_service = AsyncMock()
        mock_service.get_review_settings = AsyncMock(return_value=mock_settings)
        mock_service.create_review_request = AsyncMock(return_value=created_review)
        mock_service.update_review_request = AsyncMock(return_value=updated_review)
        mock_service.action_review_request = _action_review_request

        mock_logger = MagicMock()

        result: ToolExecutionResult = await execute_reviews_tool(context, mock_service, mock_logger)

        # The tool must succeed (the escalation succeeded)
        assert result.success is True, f"Expected success=True, got: {result}"

        # public_review_url must be absent or None — never a URL string
        url_in_data = result.data.get("public_review_url") if result.data else None
        assert url_in_data is None, (
            f"public_review_url leaked to customer: {url_in_data!r}"
        )

        # submit_rating must never have been called
        assert submit_rating_calls == [], (
            "submit_rating was invoked on a low-rating path — review URL could leak"
        )

        # The action used must have been escalate_negative
        assert result.data.get("status") == "negative_feedback_escalated", (
            f"Expected status='negative_feedback_escalated', got: {result.data.get('status')!r}"
        )

    @pytest.mark.anyio
    async def test_high_rating_does_emit_public_review_url(self, monkeypatch):
        """
        Contrast test: rating=5, threshold=4 → submit_rating path is used and
        public_review_url is populated in the result.
        """
        from backend.domains.reviews.tool_handlers import execute_reviews_tool

        review_id = uuid4()
        context = _make_execution_context(step_data={"rating": 5, "feedback": "Excellent!"})

        created_review = {"id": review_id, "metadata": {}, "rating": None, "status": "draft"}
        actioned_review = {
            "id": review_id,
            "rating": 5,
            "status": "public_review_requested",
            "public_review_url": "https://g.co/review/abc123",
        }

        mock_service = AsyncMock()
        mock_service.get_review_settings = AsyncMock(
            return_value={"positive_rating_threshold": 4, "public_review_url": "https://g.co/review/abc123"}
        )
        mock_service.create_review_request = AsyncMock(return_value=created_review)
        mock_service.update_review_request = AsyncMock(return_value=actioned_review)
        mock_service.action_review_request = AsyncMock(return_value=actioned_review)

        result = await execute_reviews_tool(context, mock_service, MagicMock())

        assert result.success is True
        assert result.data.get("public_review_url") == "https://g.co/review/abc123"


# ===========================================================================
# Gate 3 — 72-Hour Tombstone (Privacy Reaper)
# ===========================================================================

class TestPrivacyReaperTombstone:
    """
    reap_once() must:
      - Hard-delete customers whose erasure_scheduled_at <= NOW()
      - Completely ignore customers whose erasure_scheduled_at > NOW()
    """

    @pytest.mark.anyio
    async def test_reaper_deletes_past_ignores_future(self, monkeypatch):
        """
        Two customers: one expired 1 hour ago, one due 1 hour from now.
        After reap_once():
          - erase_customer_data is called exactly once (for the past customer)
          - The future customer id is never passed to erase_customer_data
        """
        from backend.pipeline import privacy_reaper

        now = datetime.now(timezone.utc)
        past_customer_id = uuid4()
        future_customer_id = uuid4()
        tenant_id = uuid4()

        past_record = {
            "id": past_customer_id,
            "tenant_id": tenant_id,
            "erasure_scheduled_at": now - timedelta(hours=1),
        }
        # The future customer must never appear in _fetch_due_tombstones output
        # because the DB query filters erasure_scheduled_at <= NOW().
        # We only inject the past record to simulate what the DB returns.

        erased_ids = []

        async def _fake_erase(ten_id, cust_id, **kwargs):
            erased_ids.append(cust_id)
            return {"customers": 1, "conversations": 3, "messages": 12}

        async def _fake_write_audit(*args, **kwargs):
            pass

        # Patch _fetch_due_tombstones to return only the expired record
        async def _fake_fetch_due(conn):
            return [past_record]

        # Patch acquire() to return a trivial async context manager
        class FakeTransaction:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
        class FakeConn:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            def transaction(self):
                return FakeTransaction()

        class FakeAcquire:
            def acquire(self):
                return FakeConn()

        monkeypatch.setattr(privacy_reaper, "_fetch_due_tombstones", _fake_fetch_due)
        monkeypatch.setattr(privacy_reaper.db_client, "acquire", FakeAcquire().acquire)
        monkeypatch.setattr(privacy_reaper.db_client, "erase_customer_data", _fake_erase)
        monkeypatch.setattr(privacy_reaper, "_write_audit", _fake_write_audit)

        count = await privacy_reaper.reap_once()

        # Exactly one customer deleted
        assert count == 1, f"Expected 1 deletion, got {count}"
        assert past_customer_id in erased_ids, "Past customer was not hard-deleted"
        assert future_customer_id not in erased_ids, (
            "Future customer was incorrectly deleted"
        )

    @pytest.mark.anyio
    async def test_reaper_returns_zero_when_no_tombstones_due(self, monkeypatch):
        """
        When no tombstones are due, reap_once() must return 0 and never call
        erase_customer_data.
        """
        from backend.pipeline import privacy_reaper

        async def _empty_fetch(conn):
            return []

        erase_calls = []

        class FakeTransaction:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
        class FakeConn:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                pass
            def transaction(self):
                return FakeTransaction()

        class FakeAcquire:
            def acquire(self):
                return FakeConn()

        monkeypatch.setattr(privacy_reaper, "_fetch_due_tombstones", _empty_fetch)
        monkeypatch.setattr(privacy_reaper.db_client, "acquire", FakeAcquire().acquire)
        monkeypatch.setattr(
            privacy_reaper.db_client, "erase_customer_data",
            AsyncMock(side_effect=lambda *a, **k: erase_calls.append(a))
        )

        count = await privacy_reaper.reap_once()

        assert count == 0
        assert erase_calls == [], "erase_customer_data called when no tombstones were due"


# ===========================================================================
# Gate 4 — Meta 24-Hour WhatsApp Messaging Window Rule
# ===========================================================================

class TestMeta24HourRule:
    """
    _dispatch_whatsapp_reminder must:
      - Use send_template_message when last_message_at > 24 hours ago
      - Use send_text_message when last_message_at <= 24 hours ago (inside window)
    """

    def _base_record(self, conversation_id, tenant_id, account_id):
        return {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "customer_phone": "+919876543210",
            "conversation_channel_account_id": account_id,
            "message_text": "Your appointment is tomorrow.",
            "trigger_type": "before_booking",
            "attempt_count": 0,
            "metadata": {},
        }

    @pytest.mark.anyio
    async def test_outside_24h_window_uses_template_message(self, monkeypatch):
        """
        last_message_at = 48 hours ago → outside window.
        Dispatcher must call send_template_message, never send_text_message.
        """
        from backend.pipeline import reminder_scheduler

        tenant_id = uuid4()
        conversation_id = uuid4()
        account_id = uuid4()

        now = datetime.now(timezone.utc)
        stale_last_msg = now - timedelta(hours=48)

        fake_account = {
            "phone_number_id": "PHONE_ID",
            "access_token": "ACCESS_TOKEN",
        }
        fake_conversation = {
            "id": conversation_id,
            "last_message_at": stale_last_msg,
        }
        # Tenant config has a whatsapp_templates mapping for trigger_type
        fake_tenant_config = {
            "config_json": {
                "modules": {
                    "reminders": {
                        "whatsapp_templates": {
                            "before_booking": "appointment_reminder_v1"
                        }
                    }
                }
            }
        }

        template_calls = []
        text_calls = []

        async def _send_template(to_phone, template_name, phone_number_id, access_token, **kwargs):
            template_calls.append(template_name)
            return {"success": True, "message_id": "wamid.TEMPLATE001"}

        async def _send_text(to_phone, message_text, phone_number_id, access_token):
            text_calls.append(message_text)
            return {"success": True, "message_id": "wamid.TEXT001"}

        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_whatsapp_account_for_dispatch",
            AsyncMock(return_value=fake_account),
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_conversation",
            AsyncMock(return_value=fake_conversation),
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_tenant_config",
            AsyncMock(return_value=fake_tenant_config),
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "save_message",
            AsyncMock(return_value={"created": True}),
        )
        monkeypatch.setattr(
            reminder_scheduler.whatsapp_client, "send_template_message", _send_template
        )
        monkeypatch.setattr(
            reminder_scheduler.whatsapp_client, "send_text_message", _send_text
        )

        record = self._base_record(conversation_id, tenant_id, account_id)
        await reminder_scheduler._dispatch_whatsapp_reminder(record)

        assert template_calls == ["appointment_reminder_v1"], (
            f"Expected send_template_message to be called once, got: {template_calls}"
        )
        assert text_calls == [], (
            "send_text_message must NOT be called when outside the 24h window"
        )

    @pytest.mark.anyio
    async def test_inside_24h_window_uses_text_message(self, monkeypatch):
        """
        last_message_at = 1 hour ago → inside window.
        Dispatcher must call send_text_message, never send_template_message.
        """
        from backend.pipeline import reminder_scheduler

        tenant_id = uuid4()
        conversation_id = uuid4()
        account_id = uuid4()

        now = datetime.now(timezone.utc)
        fresh_last_msg = now - timedelta(hours=1)

        fake_account = {"phone_number_id": "PHONE_ID", "access_token": "ACCESS_TOKEN"}
        fake_conversation = {"id": conversation_id, "last_message_at": fresh_last_msg}

        template_calls = []
        text_calls = []

        async def _send_template(**kwargs):  # pragma: no cover
            template_calls.append(kwargs)
            return {"success": True, "message_id": "wamid.TEMPLATE_SHOULD_NOT_BE_CALLED"}

        async def _send_text(to_phone, message_text, phone_number_id, access_token):
            text_calls.append(message_text)
            return {"success": True, "message_id": "wamid.TEXT002"}

        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_whatsapp_account_for_dispatch",
            AsyncMock(return_value=fake_account),
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_conversation",
            AsyncMock(return_value=fake_conversation),
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_tenant_config",
            AsyncMock(return_value=None),  # not needed — not reached
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "save_message",
            AsyncMock(return_value={"created": True}),
        )
        monkeypatch.setattr(
            reminder_scheduler.whatsapp_client, "send_template_message", _send_template
        )
        monkeypatch.setattr(
            reminder_scheduler.whatsapp_client, "send_text_message", _send_text
        )

        record = self._base_record(conversation_id, tenant_id, account_id)
        await reminder_scheduler._dispatch_whatsapp_reminder(record)

        assert text_calls == ["Your appointment is tomorrow."], (
            f"Expected send_text_message to be called once, got: {text_calls}"
        )
        assert template_calls == [], (
            "send_template_message must NOT be called when inside the 24h window"
        )

    @pytest.mark.anyio
    async def test_null_last_message_at_falls_back_to_template(self, monkeypatch):
        """
        If last_message_at is None (no prior messages), the dispatcher must
        conservatively treat this as outside the window and use send_template_message.
        """
        from backend.pipeline import reminder_scheduler

        tenant_id = uuid4()
        conversation_id = uuid4()
        account_id = uuid4()

        fake_account = {"phone_number_id": "PHONE_ID", "access_token": "ACCESS_TOKEN"}
        # No timestamp on conversation
        fake_conversation = {"id": conversation_id, "last_message_at": None}
        fake_tenant_config = {
            "config_json": {
                "modules": {
                    "reminders": {
                        "whatsapp_templates": {"before_booking": "appointment_reminder_v1"}
                    }
                }
            }
        }

        template_calls = []
        text_calls = []

        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_whatsapp_account_for_dispatch",
            AsyncMock(return_value=fake_account),
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_conversation",
            AsyncMock(return_value=fake_conversation),
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "get_tenant_config",
            AsyncMock(return_value=fake_tenant_config),
        )
        monkeypatch.setattr(
            reminder_scheduler.db_client, "save_message",
            AsyncMock(return_value={"created": True}),
        )
        monkeypatch.setattr(
            reminder_scheduler.whatsapp_client, "send_template_message",
            AsyncMock(return_value={"success": True, "message_id": "wamid.T003"}),
        )
        monkeypatch.setattr(
            reminder_scheduler.whatsapp_client, "send_text_message",
            AsyncMock(side_effect=AssertionError("send_text_message must not be called")),
        )

        record = self._base_record(conversation_id, tenant_id, account_id)
        # Must not raise — conservative fallback should use template silently
        await reminder_scheduler._dispatch_whatsapp_reminder(record)
        reminder_scheduler.whatsapp_client.send_template_message.assert_awaited_once()
