FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/app/data/appointments.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py db.py reminders.py ./

# База лежит здесь — эта папка должна сохраняться между перезапусками и деплоями
VOLUME ["/app/data"]

CMD ["python", "bot.py"]
