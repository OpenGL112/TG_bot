import asyncio
from datetime import date, datetime, time

import pytest

import db

NOW = datetime(2030, 1, 10, 12, 0)


@pytest.fixture(autouse=True)
async def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE", str(tmp_path / "test.db"))
    await db.init_db()
    await db.ensure_slots(date(2030, 1, 10), 2, time(9), time(18), 30, set(range(7)))


async def first_slot(service="Стрижка", day="2030-01-11"):
    return (await db.get_available_slots(service, day, NOW))[0][0]


async def test_ensure_slots_is_idempotent():
    added = await db.ensure_slots(date(2030, 1, 10), 2, time(9), time(18), 30, set(range(7)))
    assert added == 0


async def test_past_slots_today_are_hidden():
    slots = await db.get_available_slots("Стрижка", "2030-01-10", NOW)
    assert slots and all(t > "12:00" for _, t in slots)


async def test_slot_cannot_be_booked_twice():
    slot_id = await first_slot()
    results = await asyncio.gather(*(db.book_slot(slot_id, uid, NOW) for uid in (1, 2, 3)))
    assert sum(r is not None for r in results) == 1


async def test_cannot_cancel_foreign_booking():
    slot_id = await first_slot()
    await db.book_slot(slot_id, 1, NOW)
    booking_id = (await db.get_upcoming_bookings(1, NOW))[0][3]

    assert await db.cancel_booking(booking_id, user_id=2) is None
    assert await db.get_upcoming_bookings(1, NOW)

    assert await db.cancel_booking(booking_id, user_id=1) is not None
    assert slot_id in [s for s, _ in await db.get_available_slots("Стрижка", "2030-01-11", NOW)]
