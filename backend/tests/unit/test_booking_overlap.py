import pytest
from uuid import uuid4
from datetime import date, time
from unittest.mock import AsyncMock
from backend.domains.bookings.repository import booking_repository

class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return False

@pytest.mark.anyio
async def test_has_overlapping_booking_true(monkeypatch):
    """
    If the DB returns a row, has_overlapping_booking should return True.
    """
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value={"id": uuid4()})
    mock_acquire = FakeAcquire(mock_conn)
    monkeypatch.setattr(booking_repository.db, "acquire", lambda: mock_acquire)

    tenant_id = uuid4()
    booking_date = date(2026, 6, 28)
    booking_time = time(10, 0)
    duration_mins = 30

    result = await booking_repository.has_overlapping_booking(
        tenant_id=tenant_id,
        booking_date=booking_date,
        booking_time=booking_time,
        duration_mins=duration_mins
    )
    assert result is True

@pytest.mark.anyio
async def test_has_overlapping_booking_false(monkeypatch):
    """
    If the DB returns None, has_overlapping_booking should return False.
    """
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_acquire = FakeAcquire(mock_conn)
    monkeypatch.setattr(booking_repository.db, "acquire", lambda: mock_acquire)

    tenant_id = uuid4()
    booking_date = date(2026, 6, 28)
    booking_time = time(10, 0)
    duration_mins = 30

    result = await booking_repository.has_overlapping_booking(
        tenant_id=tenant_id,
        booking_date=booking_date,
        booking_time=booking_time,
        duration_mins=duration_mins
    )
    assert result is False

@pytest.mark.anyio
async def test_create_booking_raises_on_overlap(monkeypatch):
    """
    Booking creation should raise ValueError when there's an overlap.
    """
    mock_has_overlap = AsyncMock(return_value=True)
    monkeypatch.setattr(booking_repository, "has_overlapping_booking", mock_has_overlap)

    with pytest.raises(ValueError) as exc:
        await booking_repository.create_booking(
            tenant_id=uuid4(),
            customer_id=uuid4(),
            conversation_id=uuid4(),
            service_name="Dental Cleaning",
            booking_date=date(2026, 6, 28),
            booking_time=time(10, 0),
            booking_data={"service_duration_mins": 45}
        )
    assert "appointment time is not available" in str(exc.value)

@pytest.mark.anyio
async def test_update_booking_raises_on_overlap(monkeypatch):
    """
    Booking reschedule should raise ValueError when there's an overlap.
    """
    tenant_id = uuid4()
    booking_id = uuid4()

    mock_get_booking = AsyncMock(return_value={
        "id": booking_id,
        "booking_date": date(2026, 6, 28),
        "booking_time": time(10, 0),
        "booking_data": {"service_duration_mins": 45}
    })
    monkeypatch.setattr(booking_repository, "get_booking", mock_get_booking)

    mock_has_overlap = AsyncMock(return_value=True)
    monkeypatch.setattr(booking_repository, "has_overlapping_booking", mock_has_overlap)

    with pytest.raises(ValueError) as exc:
        await booking_repository.update_booking(
            tenant_id=tenant_id,
            booking_id=booking_id,
            booking_date=date(2026, 6, 28),
            booking_time=time(11, 0)
        )
    assert "appointment time is not available" in str(exc.value)
