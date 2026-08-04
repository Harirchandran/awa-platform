from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from backend.core.context import ConversationSession, ExecutionContext, SessionState, Tenant, TenantConfig, User
from backend.core.states import ConversationState
from backend.modules.reviews_module import reviews_module
pytestmark = pytest.mark.anyio
def _context(step_data):
    tenant_id = uuid4()
    return ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Cafe", plan_code="enterprise", status="active"),
        user=User(id=uuid4(), phone_number="+919999999999", name="Asha"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            current_module="reviews",
            state=SessionState(workflow_state=ConversationState.EXECUTING, step_data=step_data),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={"business_name": "Cafe"}),
        incoming_message={"text": "confirm"},
    )
async def test_positive_review_routes_to_external_link(monkeypatch):
    review_id = uuid4()
    monkeypatch.setattr(
        "backend.modules.reviews_module.review_service.create_review_request",
        AsyncMock(return_value={"id": review_id, "metadata": {}}),
    )
    monkeypatch.setattr(
        "backend.modules.reviews_module.review_service.action_review_request",
        AsyncMock(
            return_value={
                "id": review_id,
                "status": "public_review_requested",
                "rating": 5,
                "public_review_url": "https://maps.google.com/review-place",
            }
        ),
    )
    context = _context({"rating": 5, "feedback": "Great"})
    result = await reviews_module.execute(context)
    assert result.success is True
    assert result.data["status"] == "public_review_requested"
    assert result.data["public_review_url"] == "https://maps.google.com/review-place"
    assert "review link" in result.user_message
    assert "https://maps.google.com/review-place" in result.user_message
async def test_negative_review_uses_escalation_message(monkeypatch):
    review_id = uuid4()
    monkeypatch.setattr(
        "backend.modules.reviews_module.review_service.create_review_request",
        AsyncMock(return_value={"id": review_id, "metadata": {}}),
    )
    monkeypatch.setattr(
        "backend.modules.reviews_module.review_service.update_review_request",
        AsyncMock(return_value={"id": review_id, "metadata": {}}),
    )
    monkeypatch.setattr(
        "backend.modules.reviews_module.review_service.action_review_request",
        AsyncMock(
            return_value={
                "id": review_id,
                "status": "negative_feedback_escalated",
                "rating": 2,
                "public_review_url": None,
            }
        ),
    )
    context = _context({"rating": 2, "feedback": "Not good"})
    result = await reviews_module.execute(context)
    assert result.success is True
    assert result.data["status"] == "negative_feedback_escalated"
    assert "team" in result.user_message
