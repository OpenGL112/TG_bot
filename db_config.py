import aiosqlite
from datetime import datetime, timedelta

DATABASE = "appointments.db"

# Функция для добавления слотов в базу данных
async def fill_slots():
    async with aiosqlite.connect(DATABASE) as db:
        services = ["Стрижка", "Окрашивание", "Укладка"]
        start_time = datetime.strptime("09:00", "%H:%M")
        end_time = datetime.strptime("18:00", "%H:%M")

        # Заполнение слотов для каждой услуги
        for service in services:
            # Заполняем слоты для всех дней в текущем месяце
            for day in range(1, 32):  # Ограничиваем 31 днем, можно добавить проверку для правильных месяцев
                try:
                    date = datetime(datetime.now().year, datetime.now().month, day)
                    if date.month != datetime.now().month:  # Пропуск дней за пределами текущего месяца
                        continue

                    # Генерация слотов по времени
                    current_time = start_time
                    while current_time < end_time:
                        # Вставляем новый слот в базу
                        await db.execute("""
                            INSERT INTO slots (service, date, time)
                            VALUES (?, ?, ?)
                        """, (service, date.strftime("%Y-%m-%d"), current_time.strftime("%H:%M")))
                        current_time += timedelta(minutes=30)  # Интервал в 30 минут
                    await db.commit()
                except ValueError:
                    continue  # Пропускаем дни, которые не существуют (например, 31 февраля)

        print("Слоты успешно добавлены в базу данных.")

# Запуск функции
import asyncio
asyncio.run(fill_slots())
