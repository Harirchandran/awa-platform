import json
import re
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from backend.logic.engine.capability_menu import (
    build_capability_menu,
    build_self_serve_suggestions,
    has_order_catalog,
    hydrate_public_catalog,
)

UUID_PATTERN_STR = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


@pytest.mark.asyncio
async def test_public_chat_hydration_db_overwrites_json_when_db_has_items():
    """
    If the DB catalog has items, it must overwrite tenant JSON catalog in-memory,
    so both capability menu and suggestions reflect the DB catalog.
    """
    tenant_id = uuid4()
    config_data = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "name": "Legacy JSON Category",
                        "enabled": True,
                        "items": [
                            {"name": "Legacy JSON Item", "price": 99.0, "enabled": True}
                        ],
                    }
                ],
            }
        }
    }

    db_categories = [
        {"id": uuid4(), "name": "DB Category 1", "is_active": True}
    ]
    db_items = [
        {
            "id": uuid4(),
            "category_id": db_categories[0]["id"],
            "name": "DB Item 1",
            "price": 150.0,
            "is_active": True,
        }
    ]

    with (
        patch("backend.core.redis_client.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        mock_redis_factory.return_value = mock_redis

        mock_catalog.list_categories = AsyncMock(return_value=db_categories)
        mock_catalog.list_items = AsyncMock(return_value=db_items)

        await hydrate_public_catalog(tenant_id, config_data)

        # Check in-memory config_data is hydrated with DB categories
        categories = config_data["modules"]["order"]["categories"]
        assert len(categories) == 1
        assert categories[0]["name"] == "DB Category 1"
        assert categories[0]["items"][0]["name"] == "DB Item 1"
        assert categories[0]["items"][0]["price"] == 150.0

        # capability menu and suggestions must match DB items
        menu = build_capability_menu(config_data)
        assert "simple orders" in menu.capabilities
        assert "Show menu" in menu.suggestions
        assert "Place simple order" in menu.suggestions

        # Legacy JSON must have been fully overwritten
        assert "Legacy JSON Category" not in str(config_data)


@pytest.mark.asyncio
async def test_public_chat_hydration_falls_back_to_json_when_db_is_empty():
    """
    If the DB catalog is empty, public chat should fallback to the tenant JSON catalog.
    """
    tenant_id = uuid4()
    config_data = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "name": "Fallback JSON Category",
                        "enabled": True,
                        "items": [
                            {"name": "Fallback JSON Item", "price": 99.0, "enabled": True}
                        ],
                    }
                ],
            }
        }
    }

    with (
        patch("backend.core.redis_client.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis_factory.return_value = mock_redis

        mock_catalog.list_categories = AsyncMock(return_value=[])
        mock_catalog.list_items = AsyncMock(return_value=[])

        await hydrate_public_catalog(tenant_id, config_data)

        # The JSON config categories should remain unmodified
        categories = config_data["modules"]["order"]["categories"]
        assert len(categories) == 1
        assert categories[0]["name"] == "Fallback JSON Category"
        assert categories[0]["items"][0]["name"] == "Fallback JSON Item"


@pytest.mark.asyncio
async def test_public_chat_hydration_resilient_on_db_failures():
    """
    If the DB catalog query fails, public chat hydration must catch the exception,
    log a warning, and fallback to JSON catalog safely without crashing.
    """
    tenant_id = uuid4()
    config_data = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "name": "Fallback JSON Category",
                        "enabled": True,
                        "items": [
                            {"name": "Fallback JSON Item", "price": 99.0, "enabled": True}
                        ],
                    }
                ],
            }
        }
    }

    with (
        patch("backend.core.redis_client.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=RuntimeError("Redis down"))
        mock_redis_factory.return_value = mock_redis

        mock_catalog.list_categories = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_catalog.list_items = AsyncMock(side_effect=RuntimeError("DB down"))

        # Must not raise exception
        await hydrate_public_catalog(tenant_id, config_data)

        categories = config_data["modules"]["order"]["categories"]
        assert len(categories) == 1
        assert categories[0]["name"] == "Fallback JSON Category"


@pytest.mark.asyncio
async def test_public_chat_hydration_uses_tenant_scoped_redis_cache():
    """
    Verifies that public chat catalog hydration queries/sets cache key awa:catalog:cache:<tenant_id>.
    """
    tenant_id = uuid4()
    cache_key = f"awa:catalog:cache:{tenant_id}"
    config_data = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [],
            }
        }
    }

    cached_list = [
        {
            "id": str(uuid4()),
            "name": "Cached Category",
            "enabled": True,
            "items": [
                {
                    "id": str(uuid4()),
                    "name": "Cached Item",
                    "price": 200.0,
                    "enabled": True,
                }
            ],
        }
    ]

    with (
        patch("backend.core.redis_client.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(cached_list))
        mock_redis_factory.return_value = mock_redis

        mock_catalog.list_categories = AsyncMock()
        mock_catalog.list_items = AsyncMock()

        await hydrate_public_catalog(tenant_id, config_data)

        # Ensure DB was not queried due to cache hit
        mock_catalog.list_categories.assert_not_called()
        mock_catalog.list_items.assert_not_called()

        # Check config_data has cached values
        categories = config_data["modules"]["order"]["categories"]
        assert len(categories) == 1
        assert categories[0]["name"] == "Cached Category"
        assert categories[0]["items"][0]["name"] == "Cached Item"
        assert categories[0]["items"][0]["price"] == 200.0


@pytest.mark.asyncio
async def test_public_chat_catalog_cache_serializes_decimal_inventory_as_json_numbers():
    tenant_id = uuid4()
    config_data = {"modules": {"order": {"enabled": True, "categories": []}}}
    category_id = uuid4()
    db_categories = [{"id": category_id, "name": "DB Category", "is_active": True}]
    db_items = [
        {
            "id": uuid4(),
            "category_id": category_id,
            "name": "Measured Item",
            "price": Decimal("150.25"),
            "stock_quantity": Decimal("4.50"),
            "stock_tracking_enabled": True,
            "is_active": True,
        }
    ]

    with (
        patch("backend.core.redis_client.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        mock_redis_factory.return_value = mock_redis
        mock_catalog.list_categories = AsyncMock(return_value=db_categories)
        mock_catalog.list_items = AsyncMock(return_value=db_items)

        await hydrate_public_catalog(tenant_id, config_data)

        cached_payload = json.loads(mock_redis.set.await_args.args[1])
        cached_item = cached_payload[0]["items"][0]
        assert cached_item["price"] == 150.25
        assert cached_item["stock_quantity"] == 4.5
        assert config_data["modules"]["order"]["categories"][0]["items"][0]["stock_quantity"] == Decimal("4.50")


def test_public_chat_menu_hides_catalog_when_order_module_disabled():
    """
    Public chat capability menu and suggestions must hide order catalog options
    if the order module is disabled in tenant config.
    """
    config_disabled = {
        "modules": {
            "order": {
                "enabled": False,
                "categories": [
                    {
                        "name": "Cat",
                        "enabled": True,
                        "items": [{"name": "Item", "enabled": True}],
                    }
                ],
            }
        }
    }
    menu = build_capability_menu(config_disabled)
    assert "simple orders" not in menu.capabilities
    assert "Show menu" not in menu.suggestions
    assert "Place simple order" not in menu.suggestions


def test_public_chat_menu_hides_catalog_when_no_items_configured():
    """
    If there are no categories or items (empty catalog), public chat menu
    must not advertise the order capability.
    """
    config_empty = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [],
            }
        }
    }
    menu = build_capability_menu(config_empty)
    assert "simple orders" not in menu.capabilities
    assert "Show menu" not in menu.suggestions
    assert "Place simple order" not in menu.suggestions


def test_public_chat_safety_invariants_no_uuids_no_ids():
    """
    Verify public chat menu generation does not leak internal DB UUIDs, category IDs, or item IDs.
    """
    config_with_uuids = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "id": str(uuid4()),
                        "name": "Electronics",
                        "enabled": True,
                        "items": [
                            {
                                "id": str(uuid4()),
                                "name": "Phone",
                                "price": 499.0,
                                "sku": str(uuid4()),
                                "enabled": True,
                            }
                        ],
                    }
                ],
            }
        }
    }

    # Generate suggestions and menu text
    menu = build_capability_menu(config_with_uuids)

    # Assert no UUID patterns in customer facing outputs
    assert not UUID_PATTERN_STR.search(menu.text)
    for sug in menu.suggestions:
        assert not UUID_PATTERN_STR.search(sug)

    # Test build_catalog_choices_response formatted lines
    from backend.logic.engine.capability_menu import build_catalog_choices_response
    res = build_catalog_choices_response(config_with_uuids, ordering_mode=True)
    assert not UUID_PATTERN_STR.search(res.text)
    for qr in res.quick_replies:
        assert not UUID_PATTERN_STR.search(qr)
        assert "id" not in qr.lower()


def test_manifest_alignment():
    """
    Ensure the configuration source manifest reflects active public chat catalog integration
    and maintains the required priorities and constraints.
    """
    from backend.core.config_manifest import get_config_manifest
    manifest = get_config_manifest()

    # Find the public_chat_greeting_menu_policy domain entry
    policy_entry = next(
        (x for x in manifest if x.key == "public_chat_greeting_menu_policy"),
        None,
    )
    assert policy_entry is not None
    assert policy_entry.status == "active"
    assert "hydrated with DB catalog" in policy_entry.notes

    # Find the catalog domain entry
    catalog_entry = next((x for x in manifest if x.key == "catalog"), None)
    assert catalog_entry is not None
    assert "public chat" in catalog_entry.notes
    assert "capability_menu" in catalog_entry.runtime_consumers
    assert "/api/v1/public-chat/*" in catalog_entry.api_routes

    # Check payment provider authority remains env/provider safety-first
    payment_authority = next(
        (x for x in manifest if x.key == "payment_provider_authority"),
        None,
    )
    assert payment_authority is not None
    assert payment_authority.owner == "env_provider"
    assert payment_authority.safety_level == "payment_sensitive"
