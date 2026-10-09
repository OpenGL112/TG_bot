"""Сквозной сценарий бота через Dispatcher с фейковой сессией Telegram."""
import os
from datetime import datetime, timedelta

os.environ["BOT_TOKEN"] = os.environ.get("BOT_TOKEN") or "123456:TEST-TOKEN-TEST-TOKEN-TEST-TOKEN-TES"
os.environ["ADMIN_ID"] = os.environ.get("ADMIN_ID") or "999"

import pytest
from aiogram.client.session.base import BaseSession
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, ReplyKeyboardMarkup, Update, User

import bot as app
import db

USER = User(id=42, is_bot=False, first_name="Анна")
CHAT = Chat(id=42, type="private")


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self._next_id = 100

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            self._next_id += 1
            return Message(message_id=self._next_id, date=datetime.now(),
                           chat=Chat(id=getattr(method, "chat_id", None) or 42, type="private"),
                           text=method.text)
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""


class Harness:
    def __init__(self, session):
        self.session = session
        self.update_id = 0
        self.last_markup = None

    def _uid(self):
        self.update_id += 1
        return self.update_id

    async def text(self, text):
        self.session.calls.clear()
        msg = Message(message_id=1, date=datetime.now(), chat=CHAT, from_user=USER, text=text)
        await app.dp.feed_update(app.bot, Update(update_id=self._uid(), message=msg))
        return self._out()

    async def press(self, data):
        self.session.calls.clear()
        cq = CallbackQuery(
            id=str(self._uid()), from_user=USER, chat_instance="x", data=data,
            message=Message(message_id=50, date=datetime.now(), chat=CHAT, text="old"),
        )
        await app.dp.feed_update(app.bot, Update(update_id=self._uid(), callback_query=cq))
        return self._out()

    def _out(self):
        sent = [c for c in self.session.calls if isinstance(c, (SendMessage, EditMessageText))]
        for c in sent:
            if c.reply_markup is not None and not isinstance(c.reply_markup, ReplyKeyboardMarkup):
                self.last_markup = c.reply_markup
        return sent

    def buttons(self):
        return [b for row in self.last_markup.inline_keyboard for b in row]

    def find(self, prefix):
        return next(b.callback_data for b in self.buttons() if (b.callback_data or "").startswith(prefix))


@pytest.fixture
async def h(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATABASE", str(tmp_path / "flow.db"))
    # фиксируем «сейчас» на 10:00 сегодняшнего дня, чтобы тест не зависел от часа запуска
    clock = [app.now_local().replace(hour=10, minute=0, second=0, microsecond=0)]
    monkeypatch.setattr(app, "now_local", lambda: clock[0])
    await db.init_db()
    await app.refresh_slots()
    session = FakeSession()
    monkeypatch.setattr(app.bot, "session", session)
    await app.dp.storage.close()
    harness = Harness(session)
    harness.clock = clock
    return harness


def texts(sent):
    return "\n".join(c.text for c in sent)


async def test_full_booking_and_cancel_by_buttons(h):
    out = await h.text("/start")
    assert isinstance(out[0].reply_markup, ReplyKeyboardMarkup)

    out = await h.text(app.BTN_BOOK)
    assert "Шаг 1 из 4" in texts(out)

    out = await h.press(h.find("svc:"))
    assert "Шаг 2 из 4" in texts(out)

    out = await h.press(h.find("day:"))
    assert "Шаг 3 из 4" in texts(out)

    out = await h.press(h.find("slot:"))
    assert "Проверьте" in texts(out)

    out = await h.press("confirm")
    assert "Вы записаны" in texts(out)
    admin = [c for c in out if isinstance(c, SendMessage) and c.chat_id == app.ADMIN_ID]
    assert admin and "Новая запись" in admin[0].text

    out = await h.text(app.BTN_MY)
    assert "Ваши записи" in texts(out)

    out = await h.text(app.BTN_CANCEL)
    out = await h.press(h.find("bc:"))
    assert "Точно отменить" in texts(out)
    out = await h.press(h.find("bcy:"))
    assert "отменена" in texts(out)
    assert await db.get_upcoming_bookings(USER.id, app.now_local()) == []


async def test_calendar_navigation(h):
    await h.text(app.BTN_BOOK)
    await h.press(h.find("svc:"))
    out = await h.press(h.find("cal:"))
    assert "Выберите день" in texts(out)
    await h.press(h.find("day:"))
    assert h.find("slot:")


async def test_random_text_returns_keyboard(h):
    out = await h.text("хочу на стрижку завтра")
    assert "кнопки" in texts(out)
    assert isinstance(out[0].reply_markup, ReplyKeyboardMarkup)


async def test_stale_button_does_not_break(h):
    out = await h.press("slot:1")  # нет состояния — кнопка из старого сообщения
    assert isinstance(out[-1].reply_markup, ReplyKeyboardMarkup)


async def test_slot_taken_while_confirming(h):
    await h.text(app.BTN_BOOK)
    await h.press(h.find("svc:"))
    await h.press(h.find("day:"))
    slot_cb = h.find("slot:")
    await h.press(slot_cb)
    await db.book_slot(int(slot_cb.split(":")[1]), 777, app.now_local())  # кто-то успел раньше
    out = await h.press("confirm")
    assert "Шаг 3 из 4" in texts(out) and "только что заняли" in texts(out)
    assert slot_cb not in [b.callback_data for b in h.buttons()]


async def test_each_press_answered_once(h):
    """Telegram не разрешает отвечать на одно нажатие дважды."""
    from aiogram.methods import AnswerCallbackQuery
    await h.text(app.BTN_BOOK)
    await h.press(h.find("svc:"))
    await h.press(h.find("day:"))
    slot_cb = h.find("slot:")
    await h.press(slot_cb)
    await db.book_slot(int(slot_cb.split(":")[1]), 777, app.now_local())
    for data in ("confirm", "slot:", "back:times", "back:dates", "abort"):
        if data == "slot:":
            data = h.find("slot:")
        await h.press(data)
        answers = [c for c in h.session.calls if isinstance(c, AnswerCallbackQuery)]
        assert len(answers) == 1, data


async def book_via_buttons(h, days_ahead=2):
    """Записывается через кнопки на день через days_ahead дней, возвращает (дата, время)."""
    await h.text(app.BTN_BOOK)
    await h.press(h.find("svc:"))
    target = (h.clock[0] + timedelta(days=days_ahead)).date().isoformat()
    await h.press(f"day:{target}")
    await h.press(h.find("slot:"))
    await h.press("confirm")
    booking = (await db.get_upcoming_bookings(USER.id, h.clock[0]))[0]
    return datetime.strptime(f"{booking[1]} {booking[2]}", "%Y-%m-%d %H:%M")


def sent_to_user(h):
    return [c for c in h.session.calls if isinstance(c, SendMessage) and c.chat_id == USER.id]


async def test_reminders_end_to_end(h):
    start = await book_via_buttons(h)

    async def tick(at):
        h.clock[0] = at
        h.session.calls.clear()
        await app.send_due_reminders(at)
        return sent_to_user(h)

    assert await tick(start - timedelta(hours=25)) == []

    out = await tick(start - timedelta(hours=24))
    assert len(out) == 1 and "Напоминание" in out[0].text
    callbacks = [b.callback_data for row in out[0].reply_markup.inline_keyboard for b in row]
    assert any(c.startswith("rok:") for c in callbacks) and any(c.startswith("bc:") for c in callbacks)
    assert await tick(start - timedelta(hours=23)) == []      # не повторяется

    rok = next(c for c in callbacks if c.startswith("rok:"))
    res = await h.press(rok)
    assert "ждём вас" in texts(res)

    out = await tick(start - timedelta(hours=3))
    assert len(out) == 1 and out[0].reply_markup is None       # в день визита — без кнопок
    out = await tick(start - timedelta(hours=1))
    assert len(out) == 1 and out[0].reply_markup is None
    assert await tick(start - timedelta(minutes=30)) == []


async def test_cancel_from_day_before_reminder(h):
    start = await book_via_buttons(h)
    h.clock[0] = start - timedelta(hours=24)
    h.session.calls.clear()
    await app.send_due_reminders(h.clock[0])
    markup = sent_to_user(h)[0].reply_markup
    bc = next(b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data.startswith("bc:"))

    out = await h.press(bc)
    assert "Точно отменить" in texts(out)
    await h.press(h.find("bcy:"))
    assert await db.get_upcoming_bookings(USER.id, h.clock[0]) == []

    h.session.calls.clear()
    await app.send_due_reminders(start - timedelta(hours=3))   # отменённой записи не напоминаем
    assert sent_to_user(h) == []


async def test_blocked_user_does_not_retry(h, monkeypatch):
    from aiogram.exceptions import TelegramForbiddenError
    start = await book_via_buttons(h)
    calls = []

    async def blocked(*args, **kwargs):
        calls.append(1)
        raise TelegramForbiddenError(method=None, message="bot was blocked by the user")

    monkeypatch.setattr(app.bot, "send_message", blocked)
    await app.send_due_reminders(start - timedelta(hours=24))
    await app.send_due_reminders(start - timedelta(hours=23))
    assert len(calls) == 1


async def test_booking_message_mentions_reminders(h):
    await h.text(app.BTN_BOOK)
    await h.press(h.find("svc:"))
    target = (h.clock[0] + timedelta(days=3)).date().isoformat()
    await h.press(f"day:{target}")
    await h.press(h.find("slot:"))
    out = await h.press("confirm")
    assert "за день, за 3 часа и за час" in texts(out)
