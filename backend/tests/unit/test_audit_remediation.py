import pytest
import datetime
from uuid import uuid4
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import Request, HTTPException

from backend.pipeline.channel_output import _extract_user_message
from backend.pipeline.interactive_parser import parse_interactive_hints
from backend.domains.catalog.service import CatalogService
from backend.api.dependencies import get_current_user, _check_token_revoked


def test_dynamic_context_fallback_for_failures():
    # Test case 1: tool_key present
    payload_with_key = {
        "success": False,
        "tool_key": "booking.create_appointment"
    }
    msg_with_key = _extract_user_message(payload_with_key)
    assert "trouble with the booking system" in msg_with_key
    assert "booking" in msg_with_key

    # Test case 2: tool_key with snake case
    payload_snake_key = {
        "success": False,
        "tool_key": "lead_capture.create_lead"
    }
    msg_snake_key = _extract_user_message(payload_snake_key)
    assert "trouble with the lead capture system" in msg_snake_key

    # Test case 3: no tool_key
    payload_no_key = {
        "success": False
    }
    msg_no_key = _extract_user_message(payload_no_key)
    assert "I couldn't complete that request right now" in msg_no_key


def test_localize_hardcoded_interactive_list_strings():
    # Test default button text
    _, default_payload = parse_interactive_hints("Select: [Option 1] [Option 2] [Option 3] [Option 4]")
    assert default_payload is not None
    assert default_payload["action"]["button"] == "View Options"

    # Test localized/custom button text
    _, custom_payload = parse_interactive_hints(
        "Select: [Option 1] [Option 2] [Option 3] [Option 4]",
        button_text="See Choices"
    )
    assert custom_payload is not None
    assert custom_payload["action"]["button"] == "See Choices"


@pytest.mark.asyncio
async def test_invalidate_redis_cache_on_catalog_mutations():
    mock_repo = MagicMock()
    mock_events = AsyncMock()
    tenant_id = uuid4()
    category_id = uuid4()
    item_id = uuid4()

    # Stub repo return values with AsyncMock
    mock_repo.create_category = AsyncMock(return_value={"id": category_id, "name": "Test Category"})
    mock_repo.update_category = AsyncMock(return_value={"id": category_id, "name": "Updated Category"})
    mock_repo.delete_category = AsyncMock(return_value=True)
    mock_repo.create_item = AsyncMock(return_value={"id": item_id, "name": "Test Item"})
    mock_repo.update_item = AsyncMock(return_value={"id": item_id, "name": "Updated Item"})
    mock_repo.delete_item = AsyncMock(return_value=True)
    mock_repo.adjust_stock = AsyncMock(return_value={"id": item_id, "name": "Test Item", "stock_quantity": 10})

    service = CatalogService(repository=mock_repo, events=mock_events)

    # Patch the _invalidate_cache method to track calls
    with patch.object(service, "_invalidate_cache", new_callable=AsyncMock) as mock_invalidate:
        await service.create_category(tenant_id, name="Test Category")
        mock_invalidate.assert_called_once_with(tenant_id)
        mock_invalidate.reset_mock()

        await service.update_category(tenant_id, category_id, updates={})
        mock_invalidate.assert_called_once_with(tenant_id)
        mock_invalidate.reset_mock()

        await service.delete_category(tenant_id, category_id)
        mock_invalidate.assert_called_once_with(tenant_id)
        mock_invalidate.reset_mock()

        await service.create_item(tenant_id, name="Test Item")
        mock_invalidate.assert_called_once_with(tenant_id)
        mock_invalidate.reset_mock()

        await service.update_item(tenant_id, item_id, updates={})
        mock_invalidate.assert_called_once_with(tenant_id)
        mock_invalidate.reset_mock()

        await service.delete_item(tenant_id, item_id)
        mock_invalidate.assert_called_once_with(tenant_id)
        mock_invalidate.reset_mock()

        await service.adjust_stock(tenant_id, item_id, quantity_delta=5)
        mock_invalidate.assert_called_once_with(tenant_id)


@pytest.mark.asyncio
async def test_jwt_blocklist_dependency():
    # Test _check_token_revoked
    mock_redis = MagicMock()
    mock_redis.exists = AsyncMock(return_value=1) # 1 means key exists (revoked)
    mock_redis.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis):
        with pytest.raises(HTTPException) as exc_info:
            await _check_token_revoked("test_jti")
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Token has been revoked"

    # Test when not revoked
    mock_redis_ok = MagicMock()
    mock_redis_ok.exists = AsyncMock(return_value=0) # 0 means key does not exist
    mock_redis_ok.aclose = AsyncMock()

    with patch("redis.asyncio.from_url", return_value=mock_redis_ok):
        # Should not raise exception
        await _check_token_revoked("test_jti_ok")
