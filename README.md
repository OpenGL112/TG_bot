# TG_bot — бот записи в салон

Telegram-бот на aiogram 3 + SQLite: клиент выбирает услугу, дату и время, администратор получает уведомление о записи и отмене.

## Запуск

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить BOT_TOKEN и ADMIN_ID
./start.sh
```

База `appointments.db` создаётся автоматически. При старте и затем раз в сутки бот дополняет расписание на `BOOKING_DAYS_AHEAD` дней вперёд по рабочим часам и дням из `.env`. Старая база (без `bookings.slot_id`) мигрирует автоматически.

## Команды

`/start` — запись, `/my_bookings` — последние записи, `/cancel_bookings` — отмена предстоящих записей, `/help` — справка.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
```
