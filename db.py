import aiosqlite
from datetime import datetime

DATABASE = "appointments.db"

# Устанавливаем row_factory для получения результатов в виде словарей
async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        db.row_factory = aiosqlite.Row  # Это позволяет использовать строковые индексы как в словарях
        await db.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT,
                date TEXT,
                time TEXT,
                is_booked BOOLEAN DEFAULT FALSE
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL
            )
        """)
        await db.commit()

# Функция для инициализации базы данных
async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        # Создаем таблицу slots для слотов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT,
                date TEXT,
                time TEXT,
                is_booked BOOLEAN DEFAULT FALSE
            )
        """)

        # Создаем таблицу bookings для сохранения бронирований
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL
            )
        """)

        await db.commit()


# Функция для получения доступных слотов
async def get_available_slots(service, date):
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("""
            SELECT id, time FROM slots WHERE service = ? AND date = ? AND is_booked = FALSE
        """, (str(service), date)) as cursor:
            return await cursor.fetchall()


# Функция для бронирования слота
async def book_slot(slot_id, user_id):
    async with aiosqlite.connect(DATABASE) as db:
        # Получаем информацию о слоте, чтобы добавить запись в bookings
        async with db.execute("""
            SELECT service, date, time FROM slots WHERE id = ?
        """, (slot_id,)) as cursor:
            slot = await cursor.fetchone()

        if slot:
            service, date, time = slot
            # Добавляем запись в таблицу бронирований
            await db.execute("""
                INSERT INTO bookings (user_id, service, date, time) 
                VALUES (?, ?, ?, ?)
            """, (user_id, service, date, time))
            # Помечаем слот как забронированный
            await db.execute("""
                UPDATE slots SET is_booked = TRUE WHERE id = ?
            """, (slot_id,))
            await db.commit()


# Функция для получения последних бронирований пользователя
async def get_last_bookings(user_id: int, limit: int = 3):
    query = """
    SELECT service, date, time
    FROM bookings
    WHERE user_id = ?
    ORDER BY date DESC, time DESC
    LIMIT ?
    """
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute(query, (user_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return rows