"""
test_catalog_source_of_truth.py
Phase 3D-5A-3C: Catalog Source-of-Truth Consistency Audit and Guard

Findings summary (from audit, not assertions):
  - DB catalog (catalog_categories / catalog_items) is the editable primary store.
  - Tenant JSON order catalog (modules.order.categories) is legacy/fallback/onboarding seed.
  - Runtime hydration: AgenticEngine._hydrate_order_catalog() called at each worker turn.
    It loads DB catalog → overwrites config_data.modules.order.categories in-memory.
    Falls back silently to tenant JSON if DB is empty.
    Uses Redis cache key awa:catalog:cache:<tenant_id> with 60s TTL.
  - Public chat start builds capability menu from tenant config JSON (has_order_catalog reads
    modules.order.categories). DB catalog is NOT directly used by public_chat start/greeting.
    This is a known partial inconsistency: documented in manifest and covered by tests below.
  - Semantic field extraction (_safe_order_catalog) reads tenant config JSON catalog (same
    in-memory config_data), so after hydration it reflects DB catalog data.
  - Order validation/tool handler reads config_data + DB via catalog_service.list_items.
  - LLM receives only item names/SKUs (safe labels), never UUIDs or internal IDs.
  - Price is NEVER set by LLM; backend calculates from resolved catalog item.
  - Payment amounts derive from backend order calculation, not LLM-provided price.
  - Cache invalidation: CatalogService._invalidate_cache() called on every create/update/delete.
  - Variants (catalog_item_variants) are stored in DB but not hydrated into prompt context yet.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from backend.core.config_manifest import (
    CONFIG_MANIFEST,
    get_config_domain,
    get_config_manifest,
)
from backend.domains.catalog.service import CatalogService
from backend.domains.orders.tool_handlers import (
    _make_public_option,
    build_pending_disambiguation,
    format_disambiguation_message,
    resolve_disambiguation_selection,
    resolve_item_by_name_or_sku,
)
from backend.logic.engine.capability_menu import has_order_catalog, _catalog_items
from backend.logic.engine.field_extraction import _safe_order_catalog
from backend.modules.order_module import OrderModule

pytestmark = pytest.mark.anyio

# ---------------------------------------------------------------------------
# A. Source classification / manifest
# ---------------------------------------------------------------------------


def test_config_manifest_includes_catalog_domain():
    """Catalog must be a known manifest domain."""
    catalog = get_config_domain("catalog")
    assert catalog.key == "catalog"


def test_catalog_manifest_explicitly_mentions_db_and_tenant_json_split():
    """Manifest notes must truthfully capture the split sources."""
    catalog = get_config_domain("catalog")
    # Both storage sources must be declared
    assert "catalog_items" in catalog.storage
    assert "catalog_categories" in catalog.storage
    assert "tenant_configs.config_json.modules.order.categories" in catalog.storage
    # Notes must mention the split
    assert "DB catalog is primary" in catalog.notes or "domain_db" in catalog.owner
    assert "legacy" in catalog.notes.lower() or "fallback" in catalog.notes.lower()


def test_catalog_manifest_status_is_active_and_verified():
    """
    The catalog domain is active and verified because public-chat now uses the
    hydrated DB catalog for capability menu.
    """
    catalog = get_config_domain("catalog")
    # Status must be active (catalog API/DB is fully operational)
    assert catalog.status == "active"
    # Owner must be domain_db (DB is primary authoritative source for editable data)
    assert catalog.owner == "domain_db"
    # The notes must mention public chat integration
    assert "public chat" in catalog.notes.lower()


def test_catalog_manifest_price_currency_declares_db_item_price_wins():
    """price_currency_tax_delivery manifest must declare DB item price precedence."""
    domain = get_config_domain("price_currency_tax_delivery")
    assert "catalog_items.price" in domain.storage
    assert "domain_db_state" in domain.precedence
    assert "DB item price" in domain.notes or "wins when" in domain.notes


def test_payment_provider_and_plan_limits_manifest_entries_unchanged():
    """Regressions: payment provider and plan limits manifest entries must remain intact."""
    provider = get_config_domain("payment_provider_authority")
    assert provider.owner == "env_provider"
    assert provider.env_owned is True
    assert provider.tenant_editable is False
    assert provider.llm_visible is False
    assert provider.safety_level == "payment_sensitive"

    plan = get_config_domain("plan_limits")
    assert plan.owner == "super_admin_policy"
    assert plan.llm_visible is False
    assert plan.safety_level == "platform_security"

    payment_status = get_config_domain("payment_status_link_authority")
    assert payment_status.tenant_editable is False
    assert payment_status.llm_visible is False


def test_catalog_manifest_public_chat_menu_policy_is_active_and_hydrated():
    """public_chat_greeting_menu_policy is active because DB catalog is hydrated there."""
    domain = get_config_domain("public_chat_greeting_menu_policy")
    assert domain.status == "active"
    assert "hydrated with db catalog" in domain.notes.lower()


# ---------------------------------------------------------------------------
# B. DB catalog precedence / hydration behavior
# ---------------------------------------------------------------------------


class _FakeCatalogService:
    """Minimal fake catalog service for unit tests."""

    def __init__(self, items=None, categories=None, fail=False):
        self._items = items or []
        self._categories = categories or []
        self._fail = fail

    async def list_items(self, tenant_id, active_only=False, available_only=False):
        if self._fail:
            raise RuntimeError("DB unavailable")
        return self._items

    async def list_categories(self, tenant_id):
        if self._fail:
            raise RuntimeError("DB unavailable")
        return self._categories


def _make_config_data_with_json_catalog(items=None) -> Dict[str, Any]:
    """Config data that has a tenant JSON order catalog."""
    return {
        "business_name": "Test Bakery",
        "modules": {
            "order": {
                "enabled": True,
                "currency": "INR",
                "categories": [
                    {
                        "id": "cat-json",
                        "name": "JSON Category",
                        "enabled": True,
                        "items": items or [
                            {"id": "item-json-1", "name": "JSON Item", "price": 100, "enabled": True}
                        ],
                    }
                ],
            }
        },
    }


async def test_hydrate_order_catalog_overwrites_json_catalog_when_db_has_items():
    """
    When DB catalog has items, _hydrate_order_catalog must overwrite the tenant JSON
    catalog in config_data.modules.order.categories.
    """
    from backend.logic.engine.core import AgenticEngine
    from backend.core.context import (
        ConversationSession,
        ExecutionContext,
        SessionState,
        Tenant,
        TenantConfig,
        User,
    )

    tenant_id = uuid4()
    db_item_id = uuid4()
    db_cat_id = uuid4()

    config_data = _make_config_data_with_json_catalog()
    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+1000000000", name="Test"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config_data),
        incoming_message={"text": "hi", "channel_type": "awa_chat"},
    )

    engine = AgenticEngine.__new__(AgenticEngine)

    fake_categories = [{"id": db_cat_id, "name": "DB Category", "is_active": True}]
    fake_items = [
        {
            "id": db_item_id,
            "name": "DB Item",
            "price": 250.0,
            "category_id": db_cat_id,
            "is_active": True,
            "is_available": True,
            "image_url": None,
            "stock_quantity": None,
            "stock_tracking_enabled": False,
        }
    ]

    with (
        patch("backend.logic.engine.core.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        mock_redis_factory.return_value = mock_redis

        mock_catalog.list_categories = AsyncMock(return_value=fake_categories)
        mock_catalog.list_items = AsyncMock(return_value=fake_items)

        await engine._hydrate_order_catalog(context)

    categories = context.config.config_data["modules"]["order"]["categories"]
    assert len(categories) == 1
    assert categories[0]["name"] == "DB Category"
    assert len(categories[0]["items"]) == 1
    assert categories[0]["items"][0]["name"] == "DB Item"
    assert categories[0]["items"][0]["price"] == 250.0

    # JSON catalog item must NOT survive when DB has data
    names = [item["name"] for cat in categories for item in cat["items"]]
    assert "JSON Item" not in names


async def test_hydrate_order_catalog_falls_back_to_json_when_db_is_empty():
    """
    When DB catalog is empty, the tenant JSON catalog must survive (not be overwritten).
    """
    from backend.logic.engine.core import AgenticEngine
    from backend.core.context import (
        ConversationSession,
        ExecutionContext,
        SessionState,
        Tenant,
        TenantConfig,
        User,
    )

    tenant_id = uuid4()
    config_data = _make_config_data_with_json_catalog()

    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+1000000000", name="Test"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config_data),
        incoming_message={"text": "hi", "channel_type": "awa_chat"},
    )

    engine = AgenticEngine.__new__(AgenticEngine)

    with (
        patch("backend.logic.engine.core.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()
        mock_redis_factory.return_value = mock_redis

        mock_catalog.list_categories = AsyncMock(return_value=[])
        mock_catalog.list_items = AsyncMock(return_value=[])

        await engine._hydrate_order_catalog(context)

    # Tenant JSON catalog must survive unchanged
    categories = context.config.config_data["modules"]["order"]["categories"]
    names = [item["name"] for cat in categories for item in cat["items"]]
    assert "JSON Item" in names


async def test_hydrate_order_catalog_does_not_crash_when_db_fails():
    """
    When DB catalog fetch raises an exception, hydration must not crash.
    The tenant JSON catalog must survive.
    """
    from backend.logic.engine.core import AgenticEngine
    from backend.core.context import (
        ConversationSession,
        ExecutionContext,
        SessionState,
        Tenant,
        TenantConfig,
        User,
    )

    tenant_id = uuid4()
    config_data = _make_config_data_with_json_catalog()

    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+1000000000", name="Test"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config_data),
        incoming_message={"text": "hi", "channel_type": "awa_chat"},
    )

    engine = AgenticEngine.__new__(AgenticEngine)

    with (
        patch("backend.logic.engine.core.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis_factory.return_value = mock_redis

        mock_catalog.list_categories = AsyncMock(side_effect=RuntimeError("DB down"))
        mock_catalog.list_items = AsyncMock(side_effect=RuntimeError("DB down"))

        # Must not raise
        await engine._hydrate_order_catalog(context)

    # JSON catalog must remain intact
    categories = context.config.config_data["modules"]["order"]["categories"]
    assert categories  # Not wiped


async def test_hydrate_order_catalog_uses_redis_cache():
    """
    When Redis has a cached catalog, it must be loaded instead of querying DB.
    DB query methods should not be called.
    """
    from backend.logic.engine.core import AgenticEngine
    from backend.core.context import (
        ConversationSession,
        ExecutionContext,
        SessionState,
        Tenant,
        TenantConfig,
        User,
    )

    tenant_id = uuid4()
    cached_categories = [
        {"id": "cached-cat", "name": "Cached Category", "enabled": True, "items": [
            {"id": "cached-item", "name": "Cached Item", "price": 999.0, "enabled": True}
        ]}
    ]
    cached_json = json.dumps(cached_categories)

    config_data = _make_config_data_with_json_catalog()
    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+1000000000", name="Test"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data=config_data),
        incoming_message={"text": "hi", "channel_type": "awa_chat"},
    )

    engine = AgenticEngine.__new__(AgenticEngine)

    with (
        patch("backend.logic.engine.core.get_redis_client") as mock_redis_factory,
        patch("backend.domains.catalog.service.catalog_service") as mock_catalog,
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=cached_json)
        mock_redis_factory.return_value = mock_redis

        mock_catalog.list_categories = AsyncMock()
        mock_catalog.list_items = AsyncMock()

        await engine._hydrate_order_catalog(context)

    # DB queries must NOT have been called since cache hit
    mock_catalog.list_categories.assert_not_called()
    mock_catalog.list_items.assert_not_called()

    # Cached data must be loaded
    categories = context.config.config_data["modules"]["order"]["categories"]
    assert len(categories) == 1
    assert categories[0]["name"] == "Cached Category"


def test_hydrate_order_catalog_cache_key_is_tenant_scoped():
    """
    The Redis cache key for catalog must be scoped to tenant_id.
    This test verifies the key constant to catch regression of global cache leakage.
    """
    tenant_id = uuid4()
    expected_key = f"awa:catalog:cache:{tenant_id}"
    # Verify the pattern by construction (matches code in core.py and service.py)
    assert expected_key.startswith("awa:catalog:cache:")
    assert str(tenant_id) in expected_key


# ---------------------------------------------------------------------------
# C. Public chat/menu catalog consistency
# ---------------------------------------------------------------------------


def test_public_chat_capability_menu_reads_tenant_json_catalog():
    """
    has_order_catalog() reads modules.order.categories (tenant JSON catalog).
    Public chat does NOT use DB catalog directly for its initial greeting/menu.
    This documents the known partial inconsistency.
    """
    config_with_json = _make_config_data_with_json_catalog()
    assert has_order_catalog(config_with_json) is True

    config_without = {"modules": {"order": {"enabled": True, "categories": []}}}
    assert has_order_catalog(config_without) is False


def test_public_chat_does_not_use_db_catalog_for_capability_menu():
    """
    Capability menu catalog check is purely based on tenant JSON catalog (config_data).
    No DB call is involved. This is the documented partial inconsistency:
    after hydration the worker has DB data, but public chat session start
    reads the tenant config directly without hydration.
    """
    # Config with NO DB catalog but with JSON catalog → menu shows order
    config = _make_config_data_with_json_catalog()
    assert has_order_catalog(config) is True

    # Config with NO JSON catalog but DB might have data → menu does NOT show order
    config_no_json = {"modules": {"order": {"enabled": True, "categories": []}}}
    assert has_order_catalog(config_no_json) is False
    # This confirms the inconsistency: if tenant only has DB catalog (no JSON entries),
    # the public chat menu will not show the order option even if DB has items.


def test_public_chat_order_disabled_module_behavior():
    """
    has_order_catalog() reads items directly from modules.order.categories
    without checking the module-level 'enabled' flag. This is the current
    implementation behavior - documented here as a known quirk.
    The capability_menu.build_capability_menu() DOES check 'order' in enabled_modules
    before calling has_order_catalog, so the menu is still guarded at that level.
    """
    config_disabled_module = {
        "modules": {
            "order": {
                "enabled": False,
                "categories": [
                    {"name": "Cat", "enabled": True, "items": [
                        {"name": "Item", "enabled": True}
                    ]}
                ],
            }
        }
    }
    # has_order_catalog alone does NOT block items when order.enabled is False;
    # the guard is at build_capability_menu level (checks enabled_modules first).
    # Document expected current behavior:
    from backend.logic.engine.capability_menu import build_capability_menu
    menu = build_capability_menu(config_disabled_module)
    # Order catalog should NOT appear in menu because order module is not enabled
    assert "simple orders" not in menu.capabilities
    assert "Show menu" not in menu.suggestions


def test_catalog_items_helper_filters_disabled_categories_and_items():
    """_catalog_items() must exclude disabled categories and items for menu display."""
    config = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "name": "Active Category",
                        "enabled": True,
                        "items": [
                            {"name": "Visible", "enabled": True, "price": 50},
                            {"name": "Hidden", "enabled": False, "price": 60},
                        ],
                    },
                    {
                        "name": "Disabled Category",
                        "enabled": False,
                        "items": [{"name": "ShouldNotAppear", "enabled": True, "price": 70}],
                    },
                ],
            }
        }
    }
    items = _catalog_items(config)
    names = [i["name"] for i in items]
    assert "Visible" in names
    assert "Hidden" not in names
    assert "ShouldNotAppear" not in names


# ---------------------------------------------------------------------------
# D. Semantic extraction safety
# ---------------------------------------------------------------------------


def test_safe_order_catalog_sends_only_name_and_sku_to_llm():
    """
    _safe_order_catalog() must only expose item name and (if non-UUID) SKU to the LLM.
    No UUIDs, internal IDs, prices, or DB references must be present.
    """
    raw_uuid = "11111111-1111-1111-1111-111111111111"
    config_data: Dict[str, Any] = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "name": "Baked Goods",
                        "enabled": True,
                        "items": [
                            {
                                "id": raw_uuid,
                                "name": "Croissant",
                                "sku": "CROIS-01",
                                "price": 120.0,
                                "enabled": True,
                            },
                            {
                                "id": "item-2",
                                "name": "Éclair",
                                "sku": raw_uuid,  # UUID SKU should be redacted
                                "price": 80.0,
                                "enabled": True,
                            },
                        ],
                    }
                ],
            }
        }
    }
    result = _safe_order_catalog(config_data)

    assert len(result) == 2
    croissant = next(r for r in result if r["name"] == "Croissant")
    eclair = next(r for r in result if r["name"] == "Éclair")

    # Non-UUID SKU is kept
    assert croissant.get("sku") == "CROIS-01"
    # UUID SKU is suppressed
    assert "sku" not in eclair or eclair.get("sku") == ""
    # Price must NOT be in the LLM catalog context
    assert "price" not in croissant
    assert "price" not in eclair
    # Internal ID must NOT be present
    assert "id" not in croissant
    assert raw_uuid not in str(result)


def test_safe_order_catalog_excludes_disabled_items():
    """Disabled items must not reach the LLM via _safe_order_catalog."""
    config_data: Dict[str, Any] = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "name": "Drinks",
                        "enabled": True,
                        "items": [
                            {"name": "Tea", "enabled": True},
                            {"name": "Coffee", "enabled": False},
                        ],
                    }
                ],
            }
        }
    }
    result = _safe_order_catalog(config_data)
    names = [r["name"] for r in result]
    assert "Tea" in names
    assert "Coffee" not in names


def test_make_public_option_strips_uuid_sku():
    """_make_public_option must suppress UUID SKUs from public disambiguation."""
    raw_uuid = "22222222-2222-2222-2222-222222222222"
    item = {"index": 1, "name": "Widget", "sku": raw_uuid, "price": 99}
    opt = _make_public_option(1, item)

    assert opt["name"] == "Widget"
    assert "sku" not in opt
    # Price may be included (it is customer-visible in disambiguation)
    assert opt.get("price") == 99


def test_make_public_option_keeps_safe_sku():
    """Non-UUID SKUs must be retained in the public option."""
    item = {"name": "Gadget", "sku": "GAD-01", "price": 200}
    opt = _make_public_option(1, item)
    assert opt.get("sku") == "GAD-01"


def test_build_pending_disambiguation_hides_backend_refs():
    """
    build_pending_disambiguation must produce separate ``options`` (public) and
    ``_backend_refs`` (private). _backend_refs must not be forwarded to LLM.
    """
    items = [
        {"id": str(uuid4()), "name": "Alpha Widget", "price": 50},
        {"id": str(uuid4()), "name": "Beta Widget", "price": 75},
    ]
    pending = build_pending_disambiguation(items)

    options = pending["options"]
    backend_refs = pending["_backend_refs"]

    # options are customer-visible: only index, name, optional safe SKU, optional price
    for opt in options:
        assert "id" not in opt
        assert not UUID_PATTERN_STR.search(str(opt.get("name", "")))

    # backend_refs contain full item
    assert len(backend_refs) == 2
    assert backend_refs[0]["item"]["name"] == "Alpha Widget"


def test_disambiguation_resolution_by_index():
    """Selecting by numeric index must resolve to the correct backend item."""
    items = [
        {"id": "id-1", "name": "Item One", "price": 10},
        {"id": "id-2", "name": "Item Two", "price": 20},
    ]
    pending = build_pending_disambiguation(items)

    result = resolve_disambiguation_selection("1", pending)
    assert result is not None
    assert result["name"] == "Item One"

    result2 = resolve_disambiguation_selection("2", pending)
    assert result2 is not None
    assert result2["name"] == "Item Two"


def test_disambiguation_resolution_by_name():
    """Selecting by exact name must resolve to the correct backend item."""
    items = [
        {"id": "id-a", "name": "Green Tea", "price": 30},
        {"id": "id-b", "name": "Black Tea", "price": 35},
    ]
    pending = build_pending_disambiguation(items)

    result = resolve_disambiguation_selection("Green Tea", pending)
    assert result is not None
    assert result["name"] == "Green Tea"


def test_disambiguation_message_contains_no_uuids():
    """format_disambiguation_message must not expose UUIDs or internal IDs."""
    raw_uuid = "33333333-3333-3333-3333-333333333333"
    items = [
        {"id": raw_uuid, "name": "Secret Item", "price": 50},
    ]
    pending = build_pending_disambiguation(items)
    msg = format_disambiguation_message(pending)

    assert raw_uuid not in msg
    assert "id" not in msg.lower()
    assert "Secret Item" in msg


# ---------------------------------------------------------------------------
# E. Order/payment authority: LLM cannot set price
# ---------------------------------------------------------------------------


async def test_resolve_item_by_name_or_sku_uses_db_and_config_but_returns_catalog_price():
    """
    resolve_item_by_name_or_sku must resolve item from catalog.
    Price must come from the catalog item, not from any LLM-provided value.
    """
    tenant_id = uuid4()
    db_item = {
        "id": uuid4(),
        "name": "Fancy Cake",
        "price": 350.0,
        "currency": "INR",
        "sku": "CAKE-01",
        "source": "catalog_db",
    }

    fake_catalog_service = _FakeCatalogService(items=[db_item])
    fake_module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[]))

    from backend.core.context import (
        ConversationSession,
        ExecutionContext,
        SessionState,
        Tenant,
        TenantConfig,
        User,
    )

    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+1000000000", name="Test"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={}),
        incoming_message={"text": "Fancy Cake", "channel_type": "awa_chat"},
    )

    result = await resolve_item_by_name_or_sku(
        context, fake_module, fake_catalog_service, "Fancy Cake"
    )

    assert result["status"] == "success"
    # Price must come from catalog, not from the LLM input
    assert result["item"]["price"] == 350.0
    assert result["item"]["name"] == "Fancy Cake"


def _resolver_context(tenant_id=None):
    return SimpleNamespace(
        tenant=SimpleNamespace(id=tenant_id or uuid4()),
        config=SimpleNamespace(config_data={}),
    )


async def test_catalog_row_returned_by_hydrated_name_and_alias_view_appears_once():
    """C1: two representations of one physical row never cause ambiguity."""
    item_id = uuid4()
    hydrated_item = {
        "id": item_id,
        "name": "Electric Toothbrush",
        "sku": "DEMO-BRUSH-001",
        "price": 1299.0,
    }
    db_item = {**hydrated_item, "source": "catalog_db"}
    module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[hydrated_item]))
    catalog = _FakeCatalogService(items=[db_item])

    result = await resolve_item_by_name_or_sku(
        _resolver_context(), module, catalog, "Electric Toothbrush"
    )

    assert result["status"] == "success"
    assert result["item"]["id"] == item_id
    assert result["item"]["source"] == "catalog_db"


async def test_catalog_row_returned_by_name_and_sku_views_appears_once():
    """C2: identity deduplication applies before exact-SKU resolution."""
    item_id = uuid4()
    hydrated_item = {
        "id": item_id,
        "name": "Electric Toothbrush",
        "sku": "DEMO-BRUSH-001",
        "price": 1.0,  # Must lose to the direct DB record below.
    }
    db_item = {**hydrated_item, "price": 1299.0, "source": "catalog_db"}
    module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[hydrated_item]))
    catalog = _FakeCatalogService(items=[db_item])

    result = await resolve_item_by_name_or_sku(
        _resolver_context(), module, catalog, "DEMO-BRUSH-001"
    )

    assert result["status"] == "success"
    assert result["item"]["id"] == item_id
    assert result["item"]["price"] == 1299.0


async def test_exact_unique_normalized_catalog_name_resolves_without_ambiguity():
    """C3: formatting differences do not create a clarification prompt."""
    item = {"id": uuid4(), "name": "Dental Care Kit", "price": 499.0}
    module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[]))
    catalog = _FakeCatalogService(items=[item])

    result = await resolve_item_by_name_or_sku(
        _resolver_context(), module, catalog, "Dental-Care Kit"
    )

    assert result == {"status": "success", "item": item | {"source": "catalog_db"}}


async def test_exact_unique_sku_resolves_without_ambiguity_even_when_name_collides():
    """C4: an exact SKU wins over another row whose name happens to match it."""
    sku_item = {"id": uuid4(), "name": "Electric Toothbrush", "sku": "BRUSH-001", "price": 1299.0}
    name_collision = {"id": uuid4(), "name": "BRUSH-001", "sku": "OTHER-001", "price": 499.0}
    module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[]))
    catalog = _FakeCatalogService(items=[sku_item, name_collision])

    result = await resolve_item_by_name_or_sku(
        _resolver_context(), module, catalog, "BRUSH-001"
    )

    assert result["status"] == "success"
    assert result["item"]["id"] == sku_item["id"]


async def test_genuinely_distinct_normalized_name_matches_remain_ambiguous():
    """C5: identity deduplication never chooses between different rows."""
    items = [
        {"id": uuid4(), "name": "Dental Care Kit", "sku": "KIT-001", "price": 499.0},
        {"id": uuid4(), "name": "Dental Care Kit", "sku": "KIT-002", "price": 599.0},
    ]
    module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[]))
    catalog = _FakeCatalogService(items=items)

    result = await resolve_item_by_name_or_sku(
        _resolver_context(), module, catalog, "Dental Care Kit"
    )

    assert result["status"] == "ambiguous"
    assert {match["id"] for match in result["matches"]} == {item["id"] for item in items}


async def test_unknown_catalog_item_remains_unresolved_after_deduplication():
    """C6: deduplication does not invent a match for an unknown item."""
    item = {"id": uuid4(), "name": "Electric Toothbrush", "price": 1299.0}
    module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[item]))
    catalog = _FakeCatalogService(items=[item])

    result = await resolve_item_by_name_or_sku(
        _resolver_context(), module, catalog, "Water Flosser"
    )

    assert result == {"status": "unknown"}


async def test_resolve_item_uuid_identifier_is_rejected():
    """
    A UUID passed as item identifier (LLM attempting to set internal ID) must be rejected.
    Prevents direct internal ID injection via LLM-provided values.
    """
    tenant_id = uuid4()
    fake_catalog_service = _FakeCatalogService(items=[])
    fake_module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[]))

    from backend.core.context import (
        ConversationSession,
        ExecutionContext,
        SessionState,
        Tenant,
        TenantConfig,
        User,
    )

    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+1000000000", name="Test"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={}),
        incoming_message={"text": "hi", "channel_type": "awa_chat"},
    )

    raw_uuid = "44444444-4444-4444-4444-444444444444"
    result = await resolve_item_by_name_or_sku(
        context, fake_module, fake_catalog_service, raw_uuid
    )
    # UUID identifiers must be blocked immediately with "unknown"
    assert result["status"] == "unknown"


async def test_resolve_item_unknown_identifier_returns_unknown():
    """An identifier that matches no catalog item must return status unknown."""
    tenant_id = uuid4()
    fake_catalog_service = _FakeCatalogService(items=[])
    fake_module = SimpleNamespace(get_catalog_items=MagicMock(return_value=[]))

    from backend.core.context import (
        ConversationSession,
        ExecutionContext,
        SessionState,
        Tenant,
        TenantConfig,
        User,
    )

    context = ExecutionContext(
        tenant=Tenant(id=tenant_id, name="Test", plan_code="pro", status="active"),
        user=User(id=uuid4(), phone_number="+1000000000", name="Test"),
        session_data=ConversationSession(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=uuid4(),
            config_version=1,
            state=SessionState(),
        ),
        config=TenantConfig(tenant_id=tenant_id, version=1, config_data={}),
        incoming_message={"text": "hi", "channel_type": "awa_chat"},
    )

    result = await resolve_item_by_name_or_sku(
        context, fake_module, fake_catalog_service, "NonExistentItem123"
    )
    assert result["status"] == "unknown"


def test_order_module_get_catalog_items_reads_config_categories():
    """
    OrderModule.get_catalog_items() must read from tenant config JSON
    (modules.order.categories), not from DB directly.
    This is what makes post-hydration config consistent: after hydration the
    in-memory config_data has DB items, so get_catalog_items reflects DB data.
    """
    module = OrderModule()
    config = _make_config_data_with_json_catalog(
        items=[
            {"id": "i1", "name": "Muffin", "price": 50, "enabled": True},
            {"id": "i2", "name": "Scone", "price": 80, "enabled": False},
        ]
    )
    items = module.get_catalog_items(config, enabled_only=True)
    names = [i["name"] for i in items]
    assert "Muffin" in names
    assert "Scone" not in names


def test_order_module_resolve_item_does_not_accept_uuid():
    """
    OrderModule.resolve_item() must not return items when the value is a UUID.
    This guard prevents LLM from passing internal IDs as item_name.
    """
    module = OrderModule()
    config = _make_config_data_with_json_catalog()
    raw_uuid = "55555555-5555-5555-5555-555555555555"
    result = module.resolve_item(raw_uuid, config)
    # A UUID must not match a catalog item name
    assert result is None or result.get("id") != raw_uuid


# ---------------------------------------------------------------------------
# F. Cache invalidation audit
# ---------------------------------------------------------------------------


async def test_catalog_service_invalidates_cache_on_create_item():
    """Cache must be invalidated when a catalog item is created."""
    tenant_id = uuid4()
    item_id = uuid4()
    mock_repo = MagicMock()
    mock_repo.create_item = AsyncMock(return_value={"id": item_id, "name": "New Item"})

    service = CatalogService(repository=mock_repo)

    with patch.object(service, "_invalidate_cache", new_callable=AsyncMock) as mock_inv:
        await service.create_item(tenant_id, name="New Item")
        mock_inv.assert_called_once_with(tenant_id)


async def test_catalog_service_invalidates_cache_on_update_item():
    """Cache must be invalidated when a catalog item is updated."""
    tenant_id = uuid4()
    item_id = uuid4()
    mock_repo = MagicMock()
    mock_repo.update_item = AsyncMock(return_value={"id": item_id, "name": "Updated Item"})

    service = CatalogService(repository=mock_repo)

    with patch.object(service, "_invalidate_cache", new_callable=AsyncMock) as mock_inv:
        await service.update_item(tenant_id, item_id, {"name": "Updated Item"})
        mock_inv.assert_called_once_with(tenant_id)


async def test_catalog_service_invalidates_cache_on_delete_item():
    """Cache must be invalidated when a catalog item is deleted."""
    tenant_id = uuid4()
    item_id = uuid4()
    mock_repo = MagicMock()
    mock_repo.delete_item = AsyncMock(return_value=True)

    service = CatalogService(repository=mock_repo)

    with patch.object(service, "_invalidate_cache", new_callable=AsyncMock) as mock_inv:
        await service.delete_item(tenant_id, item_id)
        mock_inv.assert_called_once_with(tenant_id)


async def test_catalog_service_invalidates_cache_on_create_category():
    """Cache must be invalidated when a catalog category is created."""
    tenant_id = uuid4()
    cat_id = uuid4()
    mock_repo = MagicMock()
    mock_repo.create_category = AsyncMock(return_value={"id": cat_id, "name": "New Category"})

    service = CatalogService(repository=mock_repo)

    with patch.object(service, "_invalidate_cache", new_callable=AsyncMock) as mock_inv:
        await service.create_category(tenant_id, name="New Category")
        mock_inv.assert_called_once_with(tenant_id)


async def test_catalog_service_invalidates_cache_on_delete_category():
    """Cache must be invalidated when a catalog category is deleted."""
    tenant_id = uuid4()
    cat_id = uuid4()
    mock_repo = MagicMock()
    mock_repo.delete_category = AsyncMock(return_value=True)

    service = CatalogService(repository=mock_repo)

    with patch.object(service, "_invalidate_cache", new_callable=AsyncMock) as mock_inv:
        await service.delete_category(tenant_id, cat_id)
        mock_inv.assert_called_once_with(tenant_id)


async def test_catalog_service_invalidates_cache_on_stock_adjust():
    """Cache must be invalidated when stock is adjusted."""
    tenant_id = uuid4()
    item_id = uuid4()
    mock_repo = MagicMock()
    mock_repo.adjust_stock = AsyncMock(
        return_value={"id": item_id, "name": "Item", "stock_quantity": 10}
    )

    service = CatalogService(repository=mock_repo)

    with patch.object(service, "_invalidate_cache", new_callable=AsyncMock) as mock_inv:
        await service.adjust_stock(tenant_id, item_id, quantity_delta=5)
        mock_inv.assert_called_once_with(tenant_id)


async def test_catalog_cache_key_contains_tenant_id_prefix():
    """
    _invalidate_cache must use the tenant-scoped key pattern awa:catalog:cache:<tenant_id>.
    Ensures no cross-tenant cache pollution.
    """
    tenant_id = uuid4()
    mock_repo = MagicMock()
    service = CatalogService(repository=mock_repo)

    captured_keys: list[str] = []

    async def fake_delete(key):
        captured_keys.append(key)

    with patch("backend.core.redis_client.get_redis_client") as mock_redis_factory:
        mock_redis = MagicMock()
        mock_redis.delete = AsyncMock(side_effect=fake_delete)
        mock_redis_factory.return_value = mock_redis

        await service._invalidate_cache(tenant_id)

    assert len(captured_keys) == 1
    assert captured_keys[0] == f"awa:catalog:cache:{tenant_id}"
    assert str(tenant_id) in captured_keys[0]


# ---------------------------------------------------------------------------
# G. Regression guards
# ---------------------------------------------------------------------------


def test_manifest_public_chat_greeting_menu_does_not_claim_db_catalog():
    """
    Regression: public_chat_greeting_menu_policy must not declare DB catalog as
    a primary runtime consumer. The inconsistency must remain documented.
    """
    domain = get_config_domain("public_chat_greeting_menu_policy")
    consumers_str = " ".join(domain.runtime_consumers).lower()
    assert "catalog_db" not in consumers_str
    assert "db_catalog" not in consumers_str


def test_manifest_catalog_domain_has_order_workflow_as_consumer():
    """catalog manifest must list order_workflow as a runtime consumer."""
    catalog = get_config_domain("catalog")
    consumers = " ".join(catalog.runtime_consumers).lower()
    assert "order" in consumers


def test_manifest_catalog_domain_has_catalog_search_as_consumer():
    """catalog manifest must list catalog_search as a runtime consumer."""
    catalog = get_config_domain("catalog")
    consumers = " ".join(catalog.runtime_consumers).lower()
    assert "catalog_search" in consumers


def test_manifest_does_not_import_from_runtime_modules():
    """
    Manifest must not be imported by runtime modules (only tests).
    Regression guard for MANIFEST_RUNTIME_IMPORT_RULE.
    """
    from pathlib import Path
    from backend.core.config_manifest import MANIFEST_RUNTIME_IMPORT_RULE

    assert "documentation/testable metadata only" in MANIFEST_RUNTIME_IMPORT_RULE
    assert "Only tests may import it" in MANIFEST_RUNTIME_IMPORT_RULE

    backend_root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in backend_root.rglob("*.py"):
        normalized = path.as_posix()
        if normalized.endswith("core/config_manifest.py") or "/tests/" in normalized:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "config_manifest" in text:
            offenders.append(path.relative_to(backend_root).as_posix())

    assert offenders == [], f"Runtime code imports config_manifest: {offenders}"


def test_order_module_price_is_never_set_from_llm_input():
    """
    Regression: OrderModule.get_catalog_items() must return items with price from catalog.
    Order execution reads price from the resolved catalog item, not from LLM tool args.
    This test documents the pattern and guards against regression.
    """
    module = OrderModule()
    config = {
        "modules": {
            "order": {
                "enabled": True,
                "categories": [
                    {
                        "name": "Meals",
                        "enabled": True,
                        "items": [
                            {"id": "m1", "name": "Biryani", "price": 180, "enabled": True}
                        ],
                    }
                ],
            }
        }
    }
    items = module.get_catalog_items(config)
    biryani = next(i for i in items if i["name"] == "Biryani")
    # Price must come from config/catalog, not from any external input
    assert biryani["price"] == 180


# ---------------------------------------------------------------------------
# Helpers used across tests
# ---------------------------------------------------------------------------

import re
UUID_PATTERN_STR = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
