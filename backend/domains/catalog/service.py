from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.domains.catalog.repository import CatalogRepository, catalog_repository
from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.ops.logger import logger


class CatalogService:
    def __init__(self, repository: CatalogRepository = catalog_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: UUID, outcome: str, record_id: Optional[UUID] = None) -> None:
        extra = {"domain": "catalog", "action": action, "tenant_id": str(tenant_id), "outcome": outcome}
        if record_id:
            extra["record_id"] = str(record_id)
        logger.debug("domain service call", extra=extra)

    async def _invalidate_cache(self, tenant_id: UUID) -> None:
        from backend.core.redis_client import get_redis_client

        cache_key = f"awa:catalog:cache:{tenant_id}"
        try:
            redis_client = get_redis_client(socket_timeout=2)
            await redis_client.delete(cache_key)
            logger.debug(f"Invalidated catalog cache for tenant {tenant_id}")
        except Exception as exc:
            logger.error(f"Failed to invalidate catalog cache for tenant {tenant_id}: {exc}")

    # Categories
    async def list_categories(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        result = await self.repository.list_categories(tenant_id)
        self._log("list_categories", tenant_id, "success")
        return result

    async def create_category(
        self,
        tenant_id: UUID,
        name: str,
        description: Optional[str] = None,
        sort_order: int = 0,
        is_active: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        category = await self.repository.create_category(
            tenant_id=tenant_id,
            name=name,
            description=description,
            sort_order=sort_order,
            is_active=is_active,
            metadata=metadata,
        )
        if category:
            await self._invalidate_cache(tenant_id)
        if self.events and category:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="catalog_category.created",
                aggregate_type="catalog_category",
                aggregate_id=category.get("id"),
                source_domain="catalog",
                payload={"category_id": str(category.get("id")), "name": category.get("name")},
            ))
        self._log("create_category", tenant_id, "success", category.get("id"))
        return category

    async def update_category(
        self, tenant_id: UUID, category_id: UUID, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        category = await self.repository.update_category(tenant_id, category_id, updates)
        if category:
            await self._invalidate_cache(tenant_id)
            if self.events:
                await self.events.emit(BusinessEventCreate(
                    tenant_id=tenant_id,
                    event_type="catalog_category.updated",
                    aggregate_type="catalog_category",
                    aggregate_id=category.get("id"),
                    source_domain="catalog",
                    payload={"category_id": str(category.get("id")), "name": category.get("name")},
                ))
            self._log("update_category", tenant_id, "success", category_id)
        else:
            self._log("update_category", tenant_id, "not_found", category_id)
        return category

    async def delete_category(self, tenant_id: UUID, category_id: UUID) -> bool:
        result = await self.repository.delete_category(tenant_id, category_id)
        if result:
            await self._invalidate_cache(tenant_id)
        self._log("delete_category", tenant_id, "success" if result else "not_found", category_id)
        return result

    # Items
    async def list_items(
        self,
        tenant_id: UUID,
        category_id: Optional[UUID] = None,
        search: Optional[str] = None,
        active_only: bool = False,
        available_only: bool = False,
    ) -> List[Dict[str, Any]]:
        result = await self.repository.list_items(
            tenant_id, category_id=category_id, search=search, active_only=active_only, available_only=available_only
        )
        self._log("list_items", tenant_id, "success")
        return result

    async def get_item(self, tenant_id: UUID, item_id: UUID) -> Optional[Dict[str, Any]]:
        result = await self.repository.get_item(tenant_id, item_id)
        self._log("get_item", tenant_id, "success" if result else "not_found", item_id)
        return result

    async def create_item(
        self,
        tenant_id: UUID,
        name: str,
        category_id: Optional[UUID] = None,
        description: Optional[str] = None,
        price: float = 0,
        currency: str = "INR",
        sku: Optional[str] = None,
        image_url: Optional[str] = None,
        is_active: bool = True,
        is_available: bool = True,
        stock_tracking_enabled: bool = False,
        stock_quantity: float = 0,
        low_stock_threshold: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        item = await self.repository.create_item(
            tenant_id=tenant_id,
            name=name,
            category_id=category_id,
            description=description,
            price=price,
            currency=currency,
            sku=sku,
            image_url=image_url,
            is_active=is_active,
            is_available=is_available,
            stock_tracking_enabled=stock_tracking_enabled,
            stock_quantity=stock_quantity,
            low_stock_threshold=low_stock_threshold,
            metadata=metadata,
        )
        if item:
            await self._invalidate_cache(tenant_id)
        if self.events and item:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="catalog_item.created",
                aggregate_type="catalog_item",
                aggregate_id=item.get("id"),
                source_domain="catalog",
                payload={"item_id": str(item.get("id")), "name": item.get("name"), "sku": item.get("sku")},
            ))
            self._log("create_item", tenant_id, "success", item.get("id"))
        else:
            self._log("create_item", tenant_id, "failure")
        return item

    async def update_item(
        self, tenant_id: UUID, item_id: UUID, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        item = await self.repository.update_item(tenant_id, item_id, updates)
        if item:
            await self._invalidate_cache(tenant_id)
            if self.events:
                await self.events.emit(BusinessEventCreate(
                    tenant_id=tenant_id,
                    event_type="catalog_item.updated",
                    aggregate_type="catalog_item",
                    aggregate_id=item.get("id"),
                    source_domain="catalog",
                    payload={"item_id": str(item.get("id")), "name": item.get("name")},
                ))
            self._log("update_item", tenant_id, "success", item_id)
        else:
            self._log("update_item", tenant_id, "not_found", item_id)
        return item

    async def delete_item(self, tenant_id: UUID, item_id: UUID) -> bool:
        result = await self.repository.delete_item(tenant_id, item_id)
        if result:
            await self._invalidate_cache(tenant_id)
        self._log("delete_item", tenant_id, "success" if result else "not_found", item_id)
        return result

    async def adjust_stock(
        self,
        tenant_id: UUID,
        item_id: UUID,
        quantity_delta: float,
        reason: Optional[str] = None,
        event_type: str = "manual_adjustment",
        metadata: Optional[Dict[str, Any]] = None,
        created_by: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        item = await self.repository.adjust_stock(
            tenant_id, item_id, quantity_delta, reason, event_type, metadata, created_by
        )
        if item:
            await self._invalidate_cache(tenant_id)
            if self.events:
                await self.events.emit(BusinessEventCreate(
                    tenant_id=tenant_id,
                    event_type="catalog_item.stock_adjusted",
                    aggregate_type="catalog_item",
                    aggregate_id=item.get("id"),
                    source_domain="catalog",
                    payload={
                        "item_id": str(item.get("id")),
                        "quantity_delta": quantity_delta,
                        "new_stock": item.get("stock_quantity"),
                        "reason": reason
                    },
                ))
            self._log("adjust_stock", tenant_id, "success", item_id)
        else:
            self._log("adjust_stock", tenant_id, "not_found", item_id)
        return item

    async def list_inventory_events(
        self, tenant_id: UUID, item_id: UUID, limit: int = 50
    ) -> List[Dict[str, Any]]:
        result = await self.repository.list_inventory_events(tenant_id, item_id, limit)
        self._log("list_inventory_events", tenant_id, "success", item_id)
        return result

    async def resolve_catalog_item(self, tenant_id: UUID, value: Any) -> Optional[Dict[str, Any]]:
        result = await self.repository.resolve_catalog_item(tenant_id, value)
        self._log("resolve_catalog_item", tenant_id, "success" if result else "not_found")
        return result


catalog_service = CatalogService(events=event_service)
