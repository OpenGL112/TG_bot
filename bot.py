"""Telegram-бот записи на услуги салона (aiogram 3).

Всё управление — кнопками: постоянная клавиатура внизу экрана + пошаговые inline-кнопки.
Команды (/start, /help и др.) продолжают работать, но знать их не обязательно.
"""
import asyncio
import html
import logging
import os
from calendar import monthcalendar
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramForbiddenError, TelegramNotFound
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

import db
import reminders

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("bot")


# ---------------------------------------------------------------- конфигурация
def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set in the environment (см. .env.example)")
    return value


API_TOKEN = _require_env("BOT_TOKEN")
ADMIN_ID = int(_require_env("ADMIN_ID"))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Minsk"))
WORK_START = time.fromisoformat(os.getenv("WORK_START", "09:00"))
WORK_END = time.fromisoformat(os.getenv("WORK_END", "18:00"))
SLOT_MINUTES = int(os.getenv("SLOT_MINUTES", "30"))
WORK_DAYS = {int(d) for d in os.getenv("WORK_DAYS", "0,1,2,3,4,5,6").split(",") if d.strip()}
BOOKING_DAYS_AHEAD = int(os.getenv("BOOKING_DAYS_AHEAD", "30"))
SALON_NAME = os.getenv("SALON_NAME", "наш салон").strip()
SALON_URL = os.getenv("SALON_URL", "").strip()
SALON_PHONE = os.getenv("SALON_PHONE", "").strip()
SALON_ADDRESS = os.getenv("SALON_ADDRESS", "").strip()
# db импортируется до load_dotenv, поэтому путь из .env применяем здесь
db.DATABASE = os.getenv("DATABASE_PATH") or db.DATABASE

MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
             "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
SERVICE_ICONS = {"Стрижка": "✂️", "Окрашивание": "🎨", "Укладка": "💇"}

# Тексты кнопок постоянной клавиатуры
BTN_BOOK = "📅 Записаться"
BTN_MY = "📋 Мои записи"
BTN_CANCEL = "❌ Отменить запись"
BTN_HELP = "ℹ️ Помощь"

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Booking(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()
    confirming = State()


IN_BOOKING = StateFilter(Booking.choosing_service, Booking.choosing_date,
                         Booking.choosing_time, Booking.confirming)


# ---------------------------------------------------------------- утилиты
def now_local() -> datetime:
    """Текущее время в таймзоне салона (naive, для сравнения со строками в БД)."""
    return datetime.now(TZ).replace(tzinfo=None)


def human_date(iso_date: str) -> str:
    """'2026-10-09' -> 'Сегодня, пт 09.10' / 'Завтра, сб 10.10' / 'пн 12.10'."""
    d = date.fromisoformat(iso_date)
    base = f"{WEEKDAYS_RU[d.weekday()].lower()} {d.strftime('%d.%m')}"
    today = now_local().date()
    if d == today:
        return f"Сегодня, {base}"
    if d == today + timedelta(days=1):
        return f"Завтра, {base}"
    return base[0].upper() + base[1:]


def service_label(service: str) -> str:
    return f"{SERVICE_ICONS.get(service, '•')} {service}"


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def inline(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def main_keyboard() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура внизу экрана."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK)],
            [KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_CANCEL)],
            [KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Нажмите кнопку ниже 👇",
    )


async def show(event: types.Message | types.CallbackQuery, text: str,
               markup: InlineKeyboardMarkup | None = None) -> None:
    """Для нажатия кнопки — меняет текущее сообщение, иначе отправляет новое."""
    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return
        except Exception:  # сообщение старое или не изменилось — отправим новое
            pass
        await event.message.answer(text, reply_markup=markup, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=markup, parse_mode="HTML")


def step(n: int, total: int = 4) -> str:
    return f"<i>Шаг {n} из {total}</i>"


CANCEL_ROW = [btn("✖ Отменить запись", "abort")]


# ---------------------------------------------------------------- главное меню
async def send_main_menu(message: types.Message, state: FSMContext, text: str | None = None) -> None:
    await state.clear()
    await message.answer(text or "Выберите действие кнопками внизу экрана 👇",
                         reply_markup=main_keyboard())


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    name = html.escape(message.from_user.first_name or "")
    await state.clear()
    await message.answer(
        f"Здравствуйте{', ' + name if name else ''}! 👋\n\n"
        f"Я помогу записаться в {html.escape(SALON_NAME)}.\n"
        "Всё делается кнопками — <b>внизу экрана</b> 👇\n\n"
        f"Чтобы записаться, нажмите «{BTN_BOOK}».",
        reply_markup=main_keyboard(),
        parse_mode="HTML",
    )


@dp.message(Command("help"))
@dp.message(F.text == BTN_HELP)
async def on_help(message: types.Message, state: FSMContext):
    await state.clear()
    lines = [
        "<b>Как записаться</b>",
        f"1. Нажмите «{BTN_BOOK}» внизу экрана.",
        "2. Выберите услугу, день и время.",
        "3. Проверьте данные и нажмите «✅ Подтвердить».",
        "",
        f"Посмотреть свои записи — «{BTN_MY}».",
        f"Отменить запись — «{BTN_CANCEL}».",
        "",
        "Если кнопки внизу пропали, нажмите значок ⌨️ рядом с полем ввода "
        "или отправьте любое сообщение — я покажу их снова.",
    ]
    contacts = []
    if SALON_ADDRESS:
        contacts.append(f"📍 {html.escape(SALON_ADDRESS)}")
    if SALON_PHONE:
        contacts.append(f"📞 {html.escape(SALON_PHONE)}")
    if contacts:
        lines += ["", "<b>Контакты</b>", *contacts]
    markup = inline([InlineKeyboardButton(text="🌐 Наш сайт", url=SALON_URL)]) if SALON_URL else None
    await message.answer("\n".join(lines), reply_markup=markup, parse_mode="HTML")
    await message.answer("Выберите действие 👇", reply_markup=main_keyboard())


# ---------------------------------------------------------------- шаг 1: услуга
async def show_services(event: types.Message | types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(Booking.choosing_service)
    rows = [[btn(service_label(s), f"svc:{i}")] for i, s in enumerate(db.SERVICES)]
    rows.append(CANCEL_ROW)
    await show(event, f"{step(1)}\n\n<b>Какая услуга вам нужна?</b>", inline(*rows))


@dp.message(F.text == BTN_BOOK)
@dp.message(Command("book"))
async def on_book(message: types.Message, state: FSMContext):
    await show_services(message, state)


@dp.callback_query(F.data == "book")
async def on_book_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_services(callback, state)


@dp.callback_query(IN_BOOKING, F.data == "back:services")
async def on_back_services(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_services(callback, state)


@dp.callback_query(Booking.choosing_service, F.data.startswith("svc:"))
async def on_service_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        service = db.SERVICES[int(callback.data.split(":")[1])]
    except (ValueError, IndexError):
        await show_services(callback, state)
        return
    await state.update_data(service=service)
    await show_quick_dates(callback, state)


@dp.callback_query(F.data == "abort")
async def on_abort(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show(callback, "Запись не оформлена. Когда будете готовы — нажмите "
                         f"«{BTN_BOOK}» внизу экрана 👇")


# ---------------------------------------------------------------- шаг 2: дата
async def show_quick_dates(callback: types.CallbackQuery, state: FSMContext, notice: str = "") -> None:
    """Ближайшие свободные дни крупными кнопками — проще, чем календарь."""
    await state.set_state(Booking.choosing_date)
    service = (await state.get_data())["service"]
    dates = await db.get_next_free_dates(service, now_local(), limit=6)
    header = (f"{notice}\n\n" if notice else "") + f"{step(2)}\n\n{service_label(service)}\n\n"
    if not dates:
        await show(callback, header + "😔 К сожалению, свободного времени сейчас нет. "
                                      "Попробуйте выбрать другую услугу или загляните позже.",
                   inline([btn("« Другая услуга", "back:services")], CANCEL_ROW))
        return
    day_buttons = [btn(human_date(d), f"day:{d}") for d in dates]
    rows = [day_buttons[i:i + 2] for i in range(0, len(day_buttons), 2)]
    rows.append([btn("📅 Другая дата", f"cal:{dates[0][:7]}")])
    rows.append([btn("« Назад", "back:services")])
    rows.append(CANCEL_ROW)
    await show(callback, header + "<b>Выберите день</b>\nПоказаны ближайшие дни, когда есть свободное время:",
               inline(*rows))


@dp.callback_query(IN_BOOKING, F.data == "back:dates")
async def on_back_dates(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_quick_dates(callback, state)


def _month_bounds() -> tuple[str, str]:
    today = now_local().date()
    last = today + timedelta(days=BOOKING_DAYS_AHEAD - 1)
    return today.strftime("%Y-%m"), last.strftime("%Y-%m")


def _shift_month(ym: str, delta: int) -> str:
    y, m = map(int, ym.split("-"))
    m += delta
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"


@dp.callback_query(Booking.choosing_date, F.data.startswith("cal:"))
async def on_calendar(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    ym = callback.data[4:]
    first, last = _month_bounds()
    ym = min(max(ym, first), last)
    year, month = map(int, ym.split("-"))
    service = (await state.get_data())["service"]
    now = now_local()
    free_days = await db.get_days_with_free_slots(service, year, month, now)

    rows = [[btn(f"{MONTHS_RU[month]} {year}", "ignore")],
            [btn(d, "ignore") for d in WEEKDAYS_RU]]
    for week in monthcalendar(year, month):
        row = []
        for day in week:
            if day and day in free_days:
                row.append(btn(str(day), f"day:{year:04d}-{month:02d}-{day:02d}"))
            else:
                row.append(btn("·" if day else " ", "ignore"))
        rows.append(row)
    rows.append([
        btn("‹ Пред. месяц", f"cal:{_shift_month(ym, -1)}") if ym > first else btn(" ", "ignore"),
        btn("След. месяц ›", f"cal:{_shift_month(ym, 1)}") if ym < last else btn(" ", "ignore"),
    ])
    rows.append([btn("« Ближайшие дни", "back:dates")])
    rows.append(CANCEL_ROW)
    await show(callback, f"{step(2)}\n\n{service_label(service)}\n\n<b>Выберите день</b>\n"
                         "Нажимать можно на числа. Точка «·» — свободного времени нет.",
               inline(*rows))


# ---------------------------------------------------------------- шаг 3: время
async def show_times(callback: types.CallbackQuery, state: FSMContext, day_iso: str,
                     notice: str = "") -> None:
    """Список свободного времени. На callback здесь не отвечаем — это делает вызывающий хендлер."""
    data = await state.get_data()
    service = data["service"]
    slots = await db.get_available_slots(service, day_iso, now_local())
    if not slots:
        await show_quick_dates(callback, state,
                               notice="😔 На этот день свободного времени уже нет, выберите другой.")
        return
    await state.update_data(date=day_iso)
    await state.set_state(Booking.choosing_time)
    time_buttons = [btn(t, f"slot:{slot_id}") for slot_id, t in slots]
    rows = [time_buttons[i:i + 3] for i in range(0, len(time_buttons), 3)]
    rows.append([btn("« Другой день", "back:dates")])
    rows.append(CANCEL_ROW)
    await show(callback, (f"{notice}\n\n" if notice else "") + f"{step(3)}\n\n{service_label(service)}\n🗓 {human_date(day_iso)}\n\n"
                         "<b>Выберите удобное время</b>", inline(*rows))


@dp.callback_query(Booking.choosing_date, F.data.startswith("day:"))
async def on_day_chosen(callback: types.CallbackQuery, state: FSMContext):
    day_iso = callback.data[4:]
    try:
        chosen = date.fromisoformat(day_iso)
    except ValueError:
        await callback.answer()
        return
    if chosen < now_local().date():
        await callback.answer("Этот день уже прошёл", show_alert=True)
        return
    await callback.answer()
    await show_times(callback, state, day_iso)


@dp.callback_query(StateFilter(Booking.choosing_time, Booking.confirming), F.data == "back:times")
async def on_back_times(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_times(callback, state, (await state.get_data())["date"])


# ---------------------------------------------------------------- шаг 4: подтверждение
@dp.callback_query(Booking.choosing_time, F.data.startswith("slot:"))
async def on_slot_chosen(callback: types.CallbackQuery, state: FSMContext):
    try:
        slot_id = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer()
        return
    slot = await db.get_slot(slot_id)
    data = await state.get_data()
    if slot is None or slot["is_booked"] or slot["service"] != data.get("service"):
        await callback.answer("Это время уже занято, выберите другое", show_alert=True)
        await show_times(callback, state, data["date"])
        return
    await callback.answer()
    await state.update_data(slot_id=slot_id)
    await state.set_state(Booking.confirming)
    await show(
        callback,
        f"{step(4)}\n\n<b>Проверьте, всё ли верно:</b>\n\n"
        f"{service_label(slot['service'])}\n"
        f"🗓 {human_date(slot['date'])}\n"
        f"🕐 {slot['time']}",
        inline([btn("✅ Подтвердить", "confirm")],
               [btn("« Изменить время", "back:times")],
               CANCEL_ROW),
    )


@dp.callback_query(Booking.confirming, F.data == "confirm")
async def on_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    booking = await db.book_slot(data["slot_id"], callback.from_user.id, now_local())
    if booking is None:
        await callback.answer("Пока вы выбирали, это время заняли. Выберите другое 🙏", show_alert=True)
        await show_times(callback, state, data["date"],
                         notice="😔 Это время только что заняли, выберите другое.")
        return
    await callback.answer("Готово!")
    await state.clear()
    start = datetime.strptime(f"{booking['date']} {booking['time']}", "%Y-%m-%d %H:%M")
    phrase = reminders.upcoming_phrase(start, now_local())
    remind = f"Мы напомним о визите {phrase}.\n" if phrase else ""
    await show(
        callback,
        "✅ <b>Вы записаны!</b>\n\n"
        f"{service_label(booking['service'])}\n"
        f"🗓 {human_date(booking['date'])}\n"
        f"🕐 {booking['time']}\n\n"
        f"Ждём вас! {remind}"
        f"Посмотреть или отменить запись можно кнопками «{BTN_MY}» и «{BTN_CANCEL}».",
        inline([btn("📅 Записаться ещё", "book")]),
    )
    await notify_admin("Новая запись", booking, callback.from_user)


async def notify_admin(title: str, booking: dict, user: types.User) -> None:
    """Уведомление администратору. Ошибка отправки не ломает сценарий пользователя."""
    text = (
        f"<b>{html.escape(title)}</b>\n"
        f"Услуга: {html.escape(booking['service'])}\n"
        f"Дата: {date.fromisoformat(booking['date']).strftime('%d.%m.%Y')}\n"
        f"Время: {booking['time']}\n"
        f"Клиент: <a href=\"tg://user?id={user.id}\">{html.escape(user.full_name)}</a>"
    )
    if user.username:
        text += f" (@{html.escape(user.username)})"
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        logger.exception("Не удалось отправить уведомление администратору %s", ADMIN_ID)


# ---------------------------------------------------------------- мои записи
async def show_my_bookings(event: types.Message | types.CallbackQuery, user_id: int) -> None:
    upcoming = await db.get_upcoming_bookings(user_id, now_local())
    if not upcoming:
        await show(event, "У вас пока нет предстоящих записей.",
                   inline([btn("📅 Записаться", "book")]))
        return
    lines = ["<b>Ваши записи:</b>", ""]
    for service, day, slot_time, _ in upcoming:
        lines.append(f"{service_label(service)}\n🗓 {human_date(day)}, 🕐 {slot_time}\n")
    await show(event, "\n".join(lines),
               inline([btn("❌ Отменить одну из записей", "mycancel")],
                      [btn("📅 Записаться ещё", "book")]))


@dp.message(F.text == BTN_MY)
@dp.message(Command("my_bookings"))
async def on_my(message: types.Message, state: FSMContext):
    await state.clear()
    await show_my_bookings(message, message.from_user.id)


# ---------------------------------------------------------------- отмена записи
async def show_cancel_list(event: types.Message | types.CallbackQuery, user_id: int,
                           prefix: str = "") -> None:
    upcoming = await db.get_upcoming_bookings(user_id, now_local())
    if not upcoming:
        await show(event, prefix + "У вас нет предстоящих записей, отменять нечего.",
                   inline([btn("📅 Записаться", "book")]))
        return
    rows = [[btn(f"{SERVICE_ICONS.get(s, '•')} {human_date(d)}, {t}", f"bc:{bid}")]
            for s, d, t, bid in upcoming]
    rows.append([btn("« Ничего не отменять", "close")])
    await show(event, prefix + "<b>Какую запись отменить?</b>\nНажмите на нужную:", inline(*rows))


@dp.message(F.text == BTN_CANCEL)
@dp.message(Command("cancel_bookings"))
async def on_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await show_cancel_list(message, message.from_user.id)


@dp.callback_query(F.data == "mycancel")
async def on_cancel_from_list(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_cancel_list(callback, callback.from_user.id)


@dp.callback_query(F.data.startswith("bc:"))
async def on_cancel_pick(callback: types.CallbackQuery):
    try:
        booking_id = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer()
        return
    booking = await db.get_booking(booking_id, callback.from_user.id)
    if booking is None:
        await callback.answer("Запись не найдена", show_alert=True)
        await show_cancel_list(callback, callback.from_user.id)
        return
    await callback.answer()
    await show(
        callback,
        "<b>Точно отменить эту запись?</b>\n\n"
        f"{service_label(booking['service'])}\n"
        f"🗓 {human_date(booking['date'])}\n"
        f"🕐 {booking['time']}",
        inline([btn("✅ Да, отменить", f"bcy:{booking_id}")],
               [btn("« Нет, оставить", "mycancel")]),
    )


@dp.callback_query(F.data.startswith("bcy:"))
async def on_cancel_confirm(callback: types.CallbackQuery):
    try:
        booking_id = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer()
        return
    cancelled = await db.cancel_booking(booking_id, callback.from_user.id)
    if cancelled is None:
        await callback.answer("Запись не найдена", show_alert=True)
        await show_cancel_list(callback, callback.from_user.id)
        return
    await callback.answer("Запись отменена")
    await notify_admin("Запись отменена", cancelled, callback.from_user)
    upcoming = await db.get_upcoming_bookings(callback.from_user.id, now_local())
    text = (f"✅ Запись на {human_date(cancelled['date'])}, {cancelled['time']} отменена.")
    if upcoming:
        await show_cancel_list(callback, callback.from_user.id, prefix=text + "\n\n")
    else:
        await show(callback, text, inline([btn("📅 Записаться снова", "book")]))


@dp.callback_query(F.data == "close")
async def on_close(callback: types.CallbackQuery):
    await callback.answer()
    await show(callback, "Хорошо, ничего не меняем. Выберите действие кнопками внизу 👇")


# ---------------------------------------------------------------- напоминания
REMINDER_CHECK_SECONDS = 60


def reminder_text(reminder: reminders.Reminder, booking: dict) -> str:
    when = human_date(booking["date"]).split(",")[0]  # «Завтра» / «Сегодня» / «Пн 12.10»
    lines = ["🔔 <b>Напоминание о записи</b>", ""]
    if reminder.can_cancel:
        lines.append(f"{when} в <b>{booking['time']}</b> у вас запись:")
    else:
        lines.append(f"Сегодня в <b>{booking['time']}</b> ждём вас:")
    lines += [service_label(booking["service"]), f"🗓 {human_date(booking['date'])}, 🕐 {booking['time']}"]
    if SALON_ADDRESS:
        lines.append(f"📍 {html.escape(SALON_ADDRESS)}")
    if reminder.can_cancel:
        lines += ["", "Всё в силе? Если планы изменились, пожалуйста, отмените запись — "
                      "время освободится для других."]
    return "\n".join(lines)


async def send_due_reminders(now: datetime) -> int:
    """Отправляет напоминания, время которых пришло. Возвращает число отправленных."""
    horizon = max(r.before for r in reminders.REMINDERS)
    sent_count = 0
    for booking in await db.get_bookings_for_reminders(now, horizon):
        reminder, to_mark = reminders.plan(booking["start"], booking["created_at"], now, booking["sent"])
        if reminder is not None:
            markup = None
            if reminder.can_cancel:
                markup = inline([btn("✅ Приду", f"rok:{booking['id']}")],
                                [btn("❌ Отменить запись", f"bc:{booking['id']}")])
            try:
                await bot.send_message(booking["user_id"], reminder_text(reminder, booking),
                                       reply_markup=markup, parse_mode="HTML")
                sent_count += 1
            except (TelegramForbiddenError, TelegramNotFound):
                # пользователь заблокировал бота — повторять бессмысленно
                logger.info("Напоминание %s для записи %s не доставлено: бот заблокирован",
                            reminder.kind, booking["id"])
            except Exception:
                logger.exception("Ошибка отправки напоминания %s для записи %s",
                                 reminder.kind, booking["id"])
                continue  # попробуем на следующей проверке
        await db.mark_reminders(booking["id"], to_mark)
    return sent_count


async def reminders_loop() -> None:
    while True:
        try:
            await send_due_reminders(now_local())
        except Exception:
            logger.exception("Ошибка в цикле напоминаний")
        await asyncio.sleep(REMINDER_CHECK_SECONDS)


@dp.callback_query(F.data.startswith("rok:"))
async def on_reminder_ok(callback: types.CallbackQuery):
    try:
        booking_id = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer()
        return
    booking = await db.confirm_booking(booking_id, callback.from_user.id)
    if booking is None:
        await callback.answer("Эта запись уже отменена", show_alert=True)
        await show(callback, "Эта запись уже отменена.", inline([btn("📅 Записаться", "book")]))
        return
    await callback.answer("Спасибо!")
    await show(
        callback,
        "✅ <b>Отлично, ждём вас!</b>\n\n"
        f"{service_label(booking['service'])}\n"
        f"🗓 {human_date(booking['date'])}, 🕐 {booking['time']}\n\n"
        "Мы ещё напомним о визите за 3 часа и за час.",
    )


# ---------------------------------------------------------------- служебное
@dp.callback_query(F.data == "ignore")
async def on_ignore(callback: types.CallbackQuery):
    await callback.answer()


@dp.callback_query()
async def on_stale_callback(callback: types.CallbackQuery, state: FSMContext):
    """Кнопки из старых сообщений (например, после перезапуска бота)."""
    await callback.answer("Эти кнопки устарели — начнём заново", show_alert=True)
    await state.clear()
    await callback.message.answer("Выберите действие кнопками внизу экрана 👇",
                                  reply_markup=main_keyboard())


@dp.message()
async def on_any_message(message: types.Message, state: FSMContext):
    """Любой текст/стикер/фото: подсказываем про кнопки и возвращаем клавиатуру."""
    await send_main_menu(
        message, state,
        "Я понимаю только кнопки 🙂\nПожалуйста, выберите нужное действие внизу экрана 👇",
    )


# ---------------------------------------------------------------- запуск
async def refresh_slots() -> None:
    added = await db.ensure_slots(
        start=now_local().date(),
        days=BOOKING_DAYS_AHEAD,
        work_start=WORK_START,
        work_end=WORK_END,
        step_minutes=SLOT_MINUTES,
        work_days=WORK_DAYS,
    )
    if added:
        logger.info("Добавлено слотов: %s", added)


async def slots_refresher() -> None:
    """Раз в сутки дополняет расписание, чтобы окно записи всегда было BOOKING_DAYS_AHEAD дней."""
    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            await refresh_slots()
        except Exception:
            logger.exception("Ошибка при обновлении слотов")


async def main() -> None:
    await db.init_db()
    await refresh_slots()
    # В меню Telegram оставляем только «перезапуск» — всё остальное на кнопках
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="help", description="ℹ️ Помощь"),
    ])
    background = [asyncio.create_task(slots_refresher()), asyncio.create_task(reminders_loop())]
    try:
        await dp.start_polling(bot)
    finally:
        for task in background:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
