"""Прототип подсистемы расписания и бронирования АС «Репетитор.Просто».

Реализует функции из п. 4.2.1 ТЗ:
  * бронирование — вход: id слота и id ученика; выход: подтверждение
    бронирования со статусом оплаты; слот блокируется для других;
  * отмена — вход: id бронирования и причина; выход: статус отмены,
    инициируется возврат средств.

Хранилище пока в памяти; блокировка слота при конкурентном доступе
(критерий 2 из п. 2.2 ТЗ) обеспечивается замком. В рабочей версии её
заменит транзакция PostgreSQL с SELECT ... FOR UPDATE.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class PaymentStatus(str, Enum):
    AWAITING = "ожидает оплаты"
    FROZEN = "заморожено"
    PAID_OUT = "выплачено"
    REFUND_STARTED = "возврат инициирован"


class BookingStatus(str, Enum):
    ACTIVE = "активно"
    CANCELLED = "отменено"


class BookingError(Exception):
    """Базовая ошибка бронирования."""


class SlotNotFound(BookingError):
    pass


class SlotAlreadyBooked(BookingError):
    pass


class BookingNotFound(BookingError):
    pass


@dataclass
class AvailabilitySlot:
    slot_id: str
    tutor_id: str
    start: datetime
    end: datetime
    booked_by: str | None = None

    @property
    def is_free(self) -> bool:
        return self.booked_by is None


@dataclass
class Booking:
    booking_id: str
    slot_id: str
    student_id: str
    status: BookingStatus = BookingStatus.ACTIVE
    payment_status: PaymentStatus = PaymentStatus.AWAITING
    cancel_reason: str | None = None


@dataclass
class BookingService:
    _slots: dict[str, AvailabilitySlot] = field(default_factory=dict)
    _bookings: dict[str, Booking] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_slot(self, tutor_id: str, start: datetime, end: datetime) -> AvailabilitySlot:
        if end <= start:
            raise ValueError("Окончание слота должно быть позже начала")
        for s in self._slots.values():
            if s.tutor_id == tutor_id and start < s.end and s.start < end:
                raise ValueError("Слот пересекается с существующим")
        slot = AvailabilitySlot(str(uuid.uuid4()), tutor_id, start, end)
        self._slots[slot.slot_id] = slot
        return slot

    def free_slots(self, tutor_id: str) -> list[AvailabilitySlot]:
        return sorted(
            (s for s in self._slots.values() if s.tutor_id == tutor_id and s.is_free),
            key=lambda s: s.start,
        )

    def book(self, slot_id: str, student_id: str) -> Booking:
        with self._lock:
            slot = self._slots.get(slot_id)
            if slot is None:
                raise SlotNotFound(slot_id)
            if not slot.is_free:
                raise SlotAlreadyBooked(slot_id)
            slot.booked_by = student_id
            booking = Booking(str(uuid.uuid4()), slot_id, student_id)
            self._bookings[booking.booking_id] = booking
            return booking

    def mark_paid(self, booking_id: str) -> Booking:
        booking = self._get(booking_id)
        booking.payment_status = PaymentStatus.FROZEN
        return booking

    def cancel(self, booking_id: str, reason: str) -> Booking:
        with self._lock:
            booking = self._get(booking_id)
            if booking.status is BookingStatus.CANCELLED:
                return booking
            booking.status = BookingStatus.CANCELLED
            booking.cancel_reason = reason
            self._slots[booking.slot_id].booked_by = None
            if booking.payment_status is PaymentStatus.FROZEN:
                booking.payment_status = PaymentStatus.REFUND_STARTED
            return booking

    def _get(self, booking_id: str) -> Booking:
        try:
            return self._bookings[booking_id]
        except KeyError:
            raise BookingNotFound(booking_id) from None
