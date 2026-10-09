"""Сквозной сценарий бота через Dispatcher с фейковой сессией Telegram."""
import os
from datetime import datetime

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
    fixed_now = app.now_local().replace(hour=10, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(app, "now_local", lambda: fixed_now)
    await db.init_db()
    await app.refresh_slots()
    session = FakeSession()
    monkeypatch.setattr(app.bot, "session", session)
    await app.dp.storage.close()
    return Harness(session)


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
