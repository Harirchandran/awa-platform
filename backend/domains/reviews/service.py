from typing import Any, Dict, List, Optional
from uuid import UUID

from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.domains.reviews.repository import ReviewRepository, review_repository
from backend.ops.logger import logger


class ReviewService:
    def __init__(self, repository: ReviewRepository = review_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: UUID, outcome: str, record_id: Optional[UUID] = None, reason_code: Optional[str] = None) -> None:
        extra = {"domain": "reviews", "action": action, "tenant_id": str(tenant_id), "outcome": outcome}
        if record_id:
            extra["record_id"] = str(record_id)
        if reason_code:
            extra["reason_code"] = reason_code
        logger.debug("domain service call", extra=extra)

    async def get_review_settings(self, tenant_id: UUID) -> Dict[str, Any]:
        settings = await self.repository.get_review_settings(tenant_id)
        self._log("get_settings", tenant_id, "success", settings.get("id"))
        return settings

    async def update_review_settings(self, tenant_id: UUID, updates: Dict[str, Any]) -> Dict[str, Any]:
        settings = await self.repository.update_review_settings(tenant_id, updates)
        self._log("update_settings", tenant_id, "success", settings.get("id"))
        return settings

    async def list_review_requests(self, tenant_id: UUID, **filters) -> Dict[str, Any]:
        result = await self.repository.list_review_requests(tenant_id, **filters)
        self._log("list", tenant_id, "success")
        return result

    async def get_review_request(self, tenant_id: UUID, review_id: UUID) -> Optional[Dict[str, Any]]:
        review = await self.repository.get_review_request(tenant_id, review_id)
        self._log("get", tenant_id, "found" if review else "not_found", review_id)
        return review

    async def create_review_request(
        self,
        tenant_id: UUID,
        status: str = "draft",
        source: str = "manual",
        customer_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        booking_id: Optional[UUID] = None,
        order_id: Optional[UUID] = None,
        public_review_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        created_by: Optional[UUID] = None,
        emit_requested_event: bool = True,
        event_available_at=None,
        event_causation_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        review = await self.repository.create_review_request(
            tenant_id=tenant_id,
            status=status,
            source=source,
            customer_id=customer_id,
            conversation_id=conversation_id,
            booking_id=booking_id,
            order_id=order_id,
            public_review_url=public_review_url,
            metadata=metadata,
            idempotency_key=idempotency_key,
            created_by=created_by,
        )
        if review and self.events and emit_requested_event:
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="review.requested",
                aggregate_type="review_request",
                aggregate_id=review.get("id"),
                source_domain="reviews",
                conversation_id=conversation_id,
                customer_id=customer_id,
                payload={
                    "review_request_id": str(review.get("id")),
                    "status": review.get("status"),
                    "source": source,
                    "idempotency_key": idempotency_key,
                },
                correlation_id=str(conversation_id or review.get("id")),
                causation_id=event_causation_id,
                available_at=event_available_at,
            ))
        self._log("create", tenant_id, "success" if review else "not_found", review.get("id") if review else None)
        return review

    async def update_review_request(self, tenant_id: UUID, review_id: UUID, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        review = await self.repository.update_review_request(tenant_id, review_id, updates)
        self._log("update", tenant_id, "success" if review else "not_found", review_id)
        return review

    async def delete_review_request(self, tenant_id: UUID, review_id: UUID) -> bool:
        success = await self.repository.delete_review_request(tenant_id, review_id)
        self._log("delete", tenant_id, "success" if success else "not_found", review_id)
        return success

    async def get_review_metrics(self, tenant_id: UUID) -> List[Dict[str, Any]]:
        metrics = await self.repository.get_review_metrics(tenant_id)
        self._log("get_metrics", tenant_id, "success")
        return metrics

    async def action_review_request(self, tenant_id: UUID, review_id: UUID, **kwargs) -> Optional[Dict[str, Any]]:
        review = await self.repository.action_review_request(tenant_id, review_id, **kwargs)
        if review and self.events and kwargs.get("action") == "submit_rating":
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="review.submitted",
                aggregate_type="review_request",
                aggregate_id=review_id,
                source_domain="reviews",
                conversation_id=review.get("conversation_id"),
                customer_id=review.get("customer_id"),
                payload={
                    "review_request_id": str(review_id),
                    "rating": review.get("rating") or kwargs.get("rating"),
                    "status": review.get("status"),
                },
                correlation_id=str(review.get("conversation_id") or review_id),
            ))
            rating = review.get("rating") or kwargs.get("rating")
            settings = await self.get_review_settings(tenant_id)
            positive_rating_threshold = settings.get("positive_rating_threshold")
            if positive_rating_threshold is None:
                positive_rating_threshold = 4

            is_negative = False
            if rating is not None:
                try:
                    is_negative = int(rating) < int(positive_rating_threshold)
                except (ValueError, TypeError):
                    is_negative = int(rating) < 4

            if review.get("status") == "negative_feedback_escalated" or is_negative:
                await self.events.emit(BusinessEventCreate(
                    tenant_id=tenant_id,
                    event_type="review.negative",
                    aggregate_type="review_request",
                    aggregate_id=review_id,
                    source_domain="reviews",
                    conversation_id=review.get("conversation_id"),
                    customer_id=review.get("customer_id"),
                    payload={
                        "review_request_id": str(review_id),
                        "rating": rating,
                        "status": review.get("status"),
                    },
                    correlation_id=str(review.get("conversation_id") or review_id),
                ))
        self._log("action", tenant_id, "success" if review else "not_found", review_id, kwargs.get("action"))
        return review

    async def list_review_events(self, tenant_id: UUID, review_id: UUID, limit: int = 50) -> List[Dict[str, Any]]:
        events = await self.repository.list_review_events(tenant_id, review_id, limit=limit)
        self._log("list_events", tenant_id, "success", review_id)
        return events


review_service = ReviewService(events=event_service)
