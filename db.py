"""Работа с базой данных записей (SQLite)."""
import logging
import os
from datetime import date, datetime, time, timedelta

import aiosqlite

logger = logging.getLogger(__name__)

# Путь к файлу базы. В контейнере его стоит направить в папку, которая сохраняется между деплоями.
DATABASE = os.getenv("DATABASE_PATH") or "appointments.db"

SERVICES = ["Стрижка", "Окрашивание", "Укладка"]


async def _connect() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DATABASE)
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


async def init_db() -> None:
    """Создаёт таблицы и мигрирует старую схему (bookings без slot_id)."""
    folder = os.path.dirname(os.path.abspath(DATABASE))
    os.makedirs(folder, exist_ok=True)
    logger.info("База данных: %s", os.path.abspath(DATABASE))
    conn = await _connect()
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                is_booked INTEGER NOT NULL DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                slot_id INTEGER REFERENCES slots(id)
            )
        """)

        # Миграция: в старой схеме у bookings не было slot_id
        async with conn.execute("PRAGMA table_info(bookings)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "slot_id" not in columns:
            logger.info("Миграция: добавляю bookings.slot_id")
            await conn.execute("ALTER TABLE bookings ADD COLUMN slot_id INTEGER REFERENCES slots(id)")
            await conn.execute("""
                UPDATE bookings SET slot_id = (
                    SELECT s.id FROM slots s
                    WHERE s.service = bookings.service AND s.date = bookings.date AND s.time = bookings.time
                    LIMIT 1
                )
            """)

        # Миграция: колонки для напоминаний
        async with conn.execute("PRAGMA table_info(bookings)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        for column, ddl in (
            ("created_at", "TEXT"),
            ("reminded_day", "INTEGER NOT NULL DEFAULT 0"),
            ("reminded_3h", "INTEGER NOT NULL DEFAULT 0"),
            ("reminded_1h", "INTEGER NOT NULL DEFAULT 0"),
            ("confirmed", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in columns:
                await conn.execute(f"ALTER TABLE bookings ADD COLUMN {column} {ddl}")

        # Удаляем дубли слотов (оставляем занятый, иначе с меньшим id) перед созданием UNIQUE-индекса
        await conn.execute("""
            DELETE FROM slots
            WHERE EXISTS (
                SELECT 1 FROM slots s2
                WHERE s2.service = slots.service AND s2.date = slots.date AND s2.time = slots.time
                  AND (s2.is_booked > slots.is_booked
                       OR (s2.is_booked = slots.is_booked AND s2.id < slots.id))
            )
            AND id NOT IN (SELECT slot_id FROM bookings WHERE slot_id IS NOT NULL)
        """)
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_slots_service_date_time ON slots(service, date, time)"
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS ix_bookings_user ON bookings(user_id, date)")
        await conn.commit()
    finally:
        await conn.close()


async def ensure_slots(
    start: date,
    days: int,
    work_start: time,
    work_end: time,
    step_minutes: int,
    work_days: set[int],
) -> int:
    """Создаёт недостающие слоты на `days` дней вперёд начиная со `start`.

    Повторный вызов безопасен: существующие слоты не дублируются (UNIQUE + INSERT OR IGNORE).
    Возвращает количество добавленных слотов.
    """
    rows = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day.weekday() not in work_days:
            continue
        current = datetime.combine(day, work_start)
        end = datetime.combine(day, work_end)
        while current < end:
            for service in SERVICES:
                rows.append((service, day.isoformat(), current.strftime("%H:%M")))
            current += timedelta(minutes=step_minutes)

    conn = await _connect()
    try:
        before = conn.total_changes
        await conn.executemany(
            "INSERT OR IGNORE INTO slots (service, date, time) VALUES (?, ?, ?)", rows
        )
        await conn.commit()
        return conn.total_changes - before
    finally:
        await conn.close()


async def get_available_slots(service: str, day: str, now: datetime) -> list[tuple[int, str]]:
    """Свободные слоты услуги на дату. Для сегодняшнего дня прошедшее время не показывается."""
    query = "SELECT id, time FROM slots WHERE service = ? AND date = ? AND is_booked = 0"
    params: list = [service, day]
    if day == now.date().isoformat():
        query += " AND time > ?"
        params.append(now.strftime("%H:%M"))
    query += " ORDER BY time"
    conn = await _connect()
    try:
        async with conn.execute(query, params) as cur:
            return [(row[0], row[1]) for row in await cur.fetchall()]
    finally:
        await conn.close()


async def get_days_with_free_slots(service: str, year: int, month: int, now: datetime) -> set[int]:
    """Номера дней месяца, в которые у услуги есть хотя бы один свободный (не прошедший) слот."""
    prefix = f"{year:04d}-{month:02d}-%"
    conn = await _connect()
    try:
        async with conn.execute(
            """
            SELECT DISTINCT date FROM slots
            WHERE service = ? AND date LIKE ? AND is_booked = 0
              AND (date > ? OR (date = ? AND time > ?))
            """,
            (service, prefix, now.date().isoformat(), now.date().isoformat(), now.strftime("%H:%M")),
        ) as cur:
            return {int(row[0][8:10]) for row in await cur.fetchall()}
    finally:
        await conn.close()


async def book_slot(slot_id: int, user_id: int, now: datetime) -> dict | None:
    """Атомарно бронирует слот.

    Возвращает данные записи или None, если слот не существует, уже занят или в прошлом.
    """
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        cur = await conn.execute(
            """
            UPDATE slots SET is_booked = 1
            WHERE id = ? AND is_booked = 0 AND (date > ? OR (date = ? AND time > ?))
            """,
            (slot_id, now.date().isoformat(), now.date().isoformat(), now.strftime("%H:%M")),
        )
        if cur.rowcount != 1:
            await conn.rollback()
            return None
        async with conn.execute("SELECT service, date, time FROM slots WHERE id = ?", (slot_id,)) as c:
            service, day, slot_time = await c.fetchone()
        await conn.execute(
            """
            INSERT INTO bookings (user_id, service, date, time, slot_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, service, day, slot_time, slot_id, now.strftime("%Y-%m-%d %H:%M:%S")),
        )
        async with conn.execute("SELECT last_insert_rowid()") as c:
            booking_id = (await c.fetchone())[0]
        await conn.commit()
        return {"id": booking_id, "service": service, "date": day, "time": slot_time}
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def get_last_bookings(user_id: int, limit: int = 3) -> list[tuple]:
    """Последние записи пользователя: (service, date, time, booking_id)."""
    conn = await _connect()
    try:
        async with conn.execute(
            """
            SELECT service, date, time, id FROM bookings
            WHERE user_id = ? ORDER BY date DESC, time DESC LIMIT ?
            """,
            (user_id, limit),
        ) as cur:
            return list(await cur.fetchall())
    finally:
        await conn.close()


async def get_upcoming_bookings(user_id: int, now: datetime) -> list[tuple]:
    """Все будущие записи пользователя: (service, date, time, booking_id)."""
    conn = await _connect()
    try:
        async with conn.execute(
            """
            SELECT service, date, time, id FROM bookings
            WHERE user_id = ? AND (date > ? OR (date = ? AND time > ?))
            ORDER BY date, time
            """,
            (user_id, now.date().isoformat(), now.date().isoformat(), now.strftime("%H:%M")),
        ) as cur:
            return list(await cur.fetchall())
    finally:
        await conn.close()


async def cancel_booking(booking_id: int, user_id: int) -> dict | None:
    """Отменяет запись, только если она принадлежит user_id. Освобождает слот.

    Возвращает данные отменённой записи или None, если запись не найдена / чужая.
    """
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        async with conn.execute(
            "SELECT service, date, time, slot_id FROM bookings WHERE id = ? AND user_id = ?",
            (booking_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await conn.rollback()
            return None
        service, day, slot_time, slot_id = row
        if slot_id is None:  # запись из старой схемы без привязки
            async with conn.execute(
                "SELECT id FROM slots WHERE service = ? AND date = ? AND time = ?",
                (service, day, slot_time),
            ) as cur:
                found = await cur.fetchone()
            slot_id = found[0] if found else None
        await conn.execute("DELETE FROM bookings WHERE id = ? AND user_id = ?", (booking_id, user_id))
        if slot_id is not None:
            await conn.execute("UPDATE slots SET is_booked = 0 WHERE id = ?", (slot_id,))
        await conn.commit()
        return {"service": service, "date": day, "time": slot_time}
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def get_next_free_dates(service: str, now: datetime, limit: int = 6) -> list[str]:
    """Ближайшие даты (ISO), на которые у услуги есть свободное время."""
    conn = await _connect()
    try:
        async with conn.execute(
            """
            SELECT DISTINCT date FROM slots
            WHERE service = ? AND is_booked = 0
              AND (date > ? OR (date = ? AND time > ?))
            ORDER BY date LIMIT ?
            """,
            (service, now.date().isoformat(), now.date().isoformat(), now.strftime("%H:%M"), limit),
        ) as cur:
            return [row[0] for row in await cur.fetchall()]
    finally:
        await conn.close()


async def get_slot(slot_id: int) -> dict | None:
    """Данные слота по id."""
    conn = await _connect()
    try:
        async with conn.execute(
            "SELECT service, date, time, is_booked FROM slots WHERE id = ?", (slot_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {"service": row[0], "date": row[1], "time": row[2], "is_booked": bool(row[3])}
    finally:
        await conn.close()


async def get_booking(booking_id: int, user_id: int) -> dict | None:
    """Запись пользователя по id (чужие записи не возвращаются)."""
    conn = await _connect()
    try:
        async with conn.execute(
            "SELECT service, date, time FROM bookings WHERE id = ? AND user_id = ?",
            (booking_id, user_id),
        ) as cur:
            row = await cur.fetchone()
        return {"service": row[0], "date": row[1], "time": row[2]} if row else None
    finally:
        await conn.close()


REMINDER_KINDS = ("day", "3h", "1h")


async def get_bookings_for_reminders(now: datetime, horizon: timedelta) -> list[dict]:
    """Будущие записи в пределах horizon, по которым ещё не все напоминания обработаны."""
    now_s = now.strftime("%Y-%m-%d %H:%M")
    until_s = (now + horizon).strftime("%Y-%m-%d %H:%M")
    conn = await _connect()
    try:
        async with conn.execute(
            """
            SELECT id, user_id, service, date, time, created_at,
                   reminded_day, reminded_3h, reminded_1h
            FROM bookings
            WHERE date || ' ' || time > ? AND date || ' ' || time <= ?
              AND (reminded_day = 0 OR reminded_3h = 0 OR reminded_1h = 0)
            ORDER BY date, time
            """,
            (now_s, until_s),
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "user_id": r[1], "service": r[2], "date": r[3], "time": r[4],
            "start": datetime.strptime(f"{r[3]} {r[4]}", "%Y-%m-%d %H:%M"),
            "created_at": datetime.strptime(r[5], "%Y-%m-%d %H:%M:%S") if r[5] else None,
            "sent": {k for k, flag in zip(REMINDER_KINDS, r[6:9]) if flag},
        })
    return result


async def mark_reminders(booking_id: int, kinds: set[str]) -> None:
    kinds = {k for k in kinds if k in REMINDER_KINDS}  # имена колонок только из белого списка
    if not kinds:
        return
    assignments = ", ".join(f"reminded_{k} = 1" for k in sorted(kinds))
    conn = await _connect()
    try:
        await conn.execute(f"UPDATE bookings SET {assignments} WHERE id = ?", (booking_id,))
        await conn.commit()
    finally:
        await conn.close()


async def confirm_booking(booking_id: int, user_id: int) -> dict | None:
    """Клиент нажал «Приду». Возвращает запись или None, если её нет / чужая."""
    conn = await _connect()
    try:
        cur = await conn.execute(
            "UPDATE bookings SET confirmed = 1 WHERE id = ? AND user_id = ?", (booking_id, user_id)
        )
        await conn.commit()
        if cur.rowcount != 1:
            return None
    finally:
        await conn.close()
    return await get_booking(booking_id, user_id)
