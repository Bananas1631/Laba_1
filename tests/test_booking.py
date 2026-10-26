"""Модульные тесты подсистемы бронирования (п. 4.2.1 и критерий 2 п. 2.2 ТЗ)."""

import threading
from datetime import datetime, timedelta

import pytest

from backend.booking import (
    BookingService,
    BookingStatus,
    PaymentStatus,
    SlotAlreadyBooked,
    SlotNotFound,
)

START = datetime(2026, 4, 1, 18, 0)


@pytest.fixture
def service():
    return BookingService()


@pytest.fixture
def slot(service):
    return service.add_slot("tutor-1", START, START + timedelta(hours=1))


def test_booking_returns_confirmation_with_payment_status(service, slot):
    booking = service.book(slot.slot_id, "student-1")
    assert booking.status is BookingStatus.ACTIVE
    assert booking.payment_status is PaymentStatus.AWAITING
    assert service.free_slots("tutor-1") == []


def test_booked_slot_is_blocked_for_other_students(service, slot):
    service.book(slot.slot_id, "student-1")
    with pytest.raises(SlotAlreadyBooked):
        service.book(slot.slot_id, "student-2")


def test_unknown_slot(service):
    with pytest.raises(SlotNotFound):
        service.book("нет-такого", "student-1")


def test_concurrent_booking_only_one_wins(service, slot):
    winners, losers = [], []
    barrier = threading.Barrier(20)

    def attempt(i):
        barrier.wait()
        try:
            winners.append(service.book(slot.slot_id, f"student-{i}"))
        except SlotAlreadyBooked:
            losers.append(i)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1
    assert len(losers) == 19


def test_cancel_frees_slot_and_starts_refund(service, slot):
    booking = service.book(slot.slot_id, "student-1")
    service.mark_paid(booking.booking_id)
    cancelled = service.cancel(booking.booking_id, "заболел")
    assert cancelled.status is BookingStatus.CANCELLED
    assert cancelled.payment_status is PaymentStatus.REFUND_STARTED
    assert cancelled.cancel_reason == "заболел"
    assert [s.slot_id for s in service.free_slots("tutor-1")] == [slot.slot_id]


def test_overlapping_slots_rejected(service, slot):
    with pytest.raises(ValueError):
        service.add_slot("tutor-1", START + timedelta(minutes=30), START + timedelta(hours=2))
