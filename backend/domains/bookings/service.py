from typing import Any, Dict, Optional
from uuid import UUID

from backend.domains.bookings.repository import BookingRepository, booking_repository
from backend.domains.events.models import BusinessEventCreate
from backend.domains.events.service import EventService, event_service
from backend.ops.logger import logger


class BookingService:
    def __init__(self, repository: BookingRepository = booking_repository, events: Optional[EventService] = None):
        self.repository = repository
        self.events = events

    def _log(self, action: str, tenant_id: UUID, outcome: str, record_id: Optional[UUID] = None, reason_code: Optional[str] = None) -> None:
        extra = {"domain": "bookings", "action": action, "tenant_id": str(tenant_id), "outcome": outcome}
        if record_id:
            extra["record_id"] = str(record_id)
        if reason_code:
            extra["reason_code"] = reason_code
        logger.debug("domain service call", extra=extra)

    async def list_bookings(self, tenant_id: UUID, **filters) -> Dict[str, Any]:
        result = await self.repository.list_bookings(tenant_id, **filters)
        self._log("list", tenant_id, "success")
        return result

    async def get_booking(self, tenant_id: UUID, booking_id: UUID) -> Optional[Dict[str, Any]]:
        booking = await self.repository.get_booking(tenant_id, booking_id)
        self._log("get", tenant_id, "found" if booking else "not_found", booking_id)
        return booking

    async def list_busy_intervals(self, tenant_id: UUID, booking_date) -> list[Dict[str, Any]]:
        intervals = await self.repository.list_busy_intervals(tenant_id, booking_date)
        self._log("availability_read", tenant_id, "success")
        return intervals

    async def create_booking(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        conversation_id: UUID,
        service_name: str,
        booking_date,
        booking_time=None,
        booking_data: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        source_channel: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        booking = await self.repository.create_booking(
            tenant_id=tenant_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            service_name=service_name,
            booking_date=booking_date,
            booking_time=booking_time,
            booking_data=booking_data,
            idempotency_key=idempotency_key,
            **kwargs,
        )
        if self.events and booking.get("_created", True):
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="booking.created",
                aggregate_type="booking",
                aggregate_id=booking.get("id"),
                source_domain="bookings",
                conversation_id=conversation_id,
                customer_id=customer_id,
                payload={
                    "booking_id": str(booking.get("id")),
                    "customer_id": str(customer_id) if customer_id else None,
                    "conversation_id": str(conversation_id) if conversation_id else None,
                    "source_channel": source_channel,
                    "service_name": service_name,
                    "service_id": (booking_data or {}).get("service_id"),
                    "booking_date": booking_date.isoformat() if hasattr(booking_date, "isoformat") else booking_date,
                    "booking_time": booking_time.isoformat() if hasattr(booking_time, "isoformat") else booking_time,
                    "status": booking.get("status"),
                },
                correlation_id=str(conversation_id or booking.get("id")),
            ))
        self._log("create", tenant_id, "created" if booking.get("_created", True) else "idempotent_hit", booking.get("id"))
        return booking

    async def update_booking(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        status: Optional[str] = None,
        booking_date: Optional[str] = None,
        booking_time: Optional[str] = None,
    ) -> bool:
        before = await self.repository.get_booking(tenant_id, booking_id)
        updated = await self.repository.update_booking(
            tenant_id,
            booking_id,
            status=status,
            booking_date=booking_date,
            booking_time=booking_time,
        )
        if (
            updated
            and self.events
            and status == "completed"
            and before
            and before.get("status") != "completed"
        ):
            booking = await self.repository.get_booking(tenant_id, booking_id) or before
            booking_data = booking.get("booking_data") if isinstance(booking.get("booking_data"), dict) else {}
            await self.events.emit(BusinessEventCreate(
                tenant_id=tenant_id,
                event_type="booking.completed",
                aggregate_type="booking",
                aggregate_id=booking_id,
                source_domain="bookings",
                conversation_id=booking.get("conversation_id"),
                customer_id=booking.get("customer_id"),
                payload={
                    "booking_id": str(booking_id),
                    "source_channel": booking_data.get("source_channel"),
                },
                correlation_id=str(booking.get("conversation_id") or booking_id),
            ))
        self._log("update", tenant_id, "success" if updated else "not_found", booking_id)
        return updated

    async def cancel_booking(self, tenant_id: UUID, booking_id: UUID) -> bool:
        updated = await self.repository.cancel_booking(tenant_id, booking_id)
        self._log("cancel", tenant_id, "success" if updated else "not_found", booking_id)
        return updated


booking_service = BookingService(events=event_service)
