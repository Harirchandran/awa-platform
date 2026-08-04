import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from pydantic import ValidationError

from backend.schemas.review import (
    ReviewSettingsUpdateRequest,
    ReviewRequestCreateRequest,
    ReviewRequestUpdateRequest,
)
from backend.domains.reviews.service import ReviewService
from backend.domains.events.handlers import handle_review_negative

pytestmark = pytest.mark.anyio

def test_url_validation():
    # Valid URLs
    ReviewSettingsUpdateRequest(public_review_url="http://google.com")
    ReviewSettingsUpdateRequest(public_review_url="https://google.com")
    ReviewSettingsUpdateRequest(public_review_url=None)
    ReviewSettingsUpdateRequest(public_review_url="")

    # Invalid URLs (must start with http:// or https://)
    with pytest.raises(ValidationError):
        ReviewSettingsUpdateRequest(public_review_url="ftp://google.com")
    with pytest.raises(ValidationError):
        ReviewSettingsUpdateRequest(public_review_url="google.com")

    # Create request validation
    ReviewRequestCreateRequest(source="manual", public_review_url="https://google.com", customer_id=uuid4())
    with pytest.raises(ValidationError):
        ReviewRequestCreateRequest(source="manual", public_review_url="invalid_url", customer_id=uuid4())

    # Update request validation
    ReviewRequestUpdateRequest(public_review_url="https://google.com")
    with pytest.raises(ValidationError):
        ReviewRequestUpdateRequest(public_review_url="invalid_url")


async def test_dynamic_threshold():
    # Setup mocks for repository/events
    repo = AsyncMock()
    events = AsyncMock()
    service = ReviewService(repository=repo, events=events)

    # Mock settings returned
    repo.get_review_settings.return_value = {
        "positive_rating_threshold": 5  # ratings < 5 are negative
    }

    # Submit rating action (rating = 4)
    repo.action_review_request.return_value = {
        "id": uuid4(),
        "status": "responded",
        "rating": 4,
        "conversation_id": uuid4(),
        "customer_id": uuid4(),
    }

    await service.action_review_request(
        tenant_id=uuid4(),
        review_id=uuid4(),
        action="submit_rating",
        rating=4,
    )

    # Verify that review.negative was emitted because rating 4 < threshold 5
    emitted_types = [call[0][0].event_type for call in events.emit.call_args_list]
    assert "review.negative" in emitted_types


async def test_orphan_negative_review_logged_to_audit():
    # Mock db_client.write_audit_log directly
    with patch("backend.db.client.db_client.write_audit_log", new_callable=AsyncMock) as mock_write_audit:
        event = {
            "tenant_id": uuid4(),
            "conversation_id": None,  # orphan
            "payload": {
                "rating": 2,
                "review_request_id": uuid4(),
            }
        }

        result = await handle_review_negative(event)
        assert result.status == "processed"
        assert result.metadata["note"] == "Orphan review logged to audit"
        mock_write_audit.assert_called_once()
