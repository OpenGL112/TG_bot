"""Telegram-бот записи на услуги салона (aiogram 3)."""
import asyncio
import html
import logging
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from calendar import monthcalendar
from dotenv import load_dotenv

import db

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
SALON_URL = os.getenv("SALON_URL", "").strip()

MONTHS_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Booking(StatesGroup):
    choosing_service = State()
    choosing_date = State()
    choosing_time = State()


def now_local() -> datetime:
    """Текущее время в таймзоне салона (naive, для сравнения со строками в БД)."""
    return datetime.now(TZ).replace(tzinfo=None)


def fmt_date(iso_date: str) -> str:
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d.%m.%Y")


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


async def show_or_edit(event: types.Message | types.CallbackQuery, text: str,
                       markup: InlineKeyboardMarkup | None = None, parse_mode: str | None = None) -> None:
    """Для callback редактирует текущее сообщение, для команды отправляет новое."""
    if isinstance(event, types.CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
            return
        except Exception:  # сообщение слишком старое/не изменилось — отправим новое
            pass
        await event.message.answer(text, reply_markup=markup, parse_mode=parse_mode)
    else:
        await event.answer(text, reply_markup=markup, parse_mode=parse_mode)


# ---------------------------------------------------------------- главное меню
def main_menu_markup() -> InlineKeyboardMarkup:
    rows = [
        [btn("Записаться", "menu:services")],
        [btn("Мои записи", "menu:my")],
        [btn("Отменить запись", "menu:cancel")],
    ]
    if SALON_URL:
        rows.append([InlineKeyboardButton(text="Наш сайт", url=SALON_URL)])
    rows.append([btn("Выход", "menu:exit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_main_menu(event: types.Message | types.CallbackQuery, state: FSMContext,
                         prefix: str = "") -> None:
    await state.clear()
    await show_or_edit(event, f"{prefix}Главное меню:", main_menu_markup())


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await show_main_menu(message, state)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "/start — запись на услугу\n"
        "/my_bookings — ваши последние записи\n"
        "/cancel_bookings — отмена предстоящих записей\n"
        "/help — эта справка"
    )


@dp.callback_query(F.data == "menu:exit")
async def on_exit(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await show_or_edit(callback, "До встречи! Чтобы начать снова, нажмите /start")


@dp.callback_query(F.data == "menu:main")
async def on_main(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await show_main_menu(callback, state)


# ---------------------------------------------------------------- выбор услуги
@dp.callback_query(F.data == "menu:services")
async def on_services(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    rows = [[btn(name, f"svc:{i}")] for i, name in enumerate(db.SERVICES)]
    rows.append([btn("« Назад", "menu:main")])
    await show_or_edit(callback, "Выберите услугу:", InlineKeyboardMarkup(inline_keyboard=rows))
    await state.set_state(Booking.choosing_service)


@dp.callback_query(Booking.choosing_service, F.data.startswith("svc:"))
async def on_service_chosen(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    try:
        service = db.SERVICES[int(callback.data.split(":")[1])]
    except (ValueError, IndexError):
        await show_main_menu(callback, state, "Неизвестная услуга.\n")
        return
    await state.update_data(service=service)
    await state.set_state(Booking.choosing_date)
    today = now_local()
    await show_calendar(callback, service, today.year, today.month)


# ---------------------------------------------------------------- календарь
def _month_bounds() -> tuple[tuple[int, int], tuple[int, int]]:
    """Первый и последний месяцы, доступные для навигации."""
    today = now_local().date()
    last = today + timedelta(days=BOOKING_DAYS_AHEAD - 1)
    return (today.year, today.month), (last.year, last.month)


async def generate_calendar(service: str, year: int, month: int) -> InlineKeyboardMarkup:
    now = now_local()
    today = now.date()
    free_days = await db.get_days_with_free_slots(service, year, month, now)

    keyboard = [
        [btn(f"{MONTHS_RU[month]} {year}", "ignore")],
        [btn(d, "ignore") for d in WEEKDAYS_RU],
    ]
    for week in monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(btn(" ", "ignore"))
            elif datetime(year, month, day).date() < today:
                row.append(btn("🔒", "ignore"))
            elif day in free_days:
                row.append(btn(str(day), f"day:{year}:{month}:{day}"))
            else:
                row.append(btn("✖", "ignore"))
        keyboard.append(row)

    first, last = _month_bounds()
    nav = []
    nav.append(btn("‹", f"cal:{year}:{month}:prev") if (year, month) > first else btn(" ", "ignore"))
    nav.append(btn("›", f"cal:{year}:{month}:next") if (year, month) < last else btn(" ", "ignore"))
    keyboard.append(nav)
    keyboard.append([btn("« К услугам", "menu:services"), btn("Отмена", "menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def show_calendar(event: types.CallbackQuery, service: str, year: int, month: int) -> None:
    markup = await generate_calendar(service, year, month)
    await show_or_edit(
        event,
        f"Услуга: {service}\nВыберите дату (✖ — нет свободного времени):",
        markup,
    )


@dp.callback_query(Booking.choosing_date, F.data.startswith("cal:"))
async def on_calendar_nav(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    _, year_s, month_s, direction = callback.data.split(":")
    year, month = int(year_s), int(month_s)
    if direction == "next":
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    else:
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)

    first, last = _month_bounds()
    if not first <= (year, month) <= last:
        return
    data = await state.get_data()
    await show_calendar(callback, data["service"], year, month)


@dp.callback_query(Booking.choosing_date, F.data.startswith("day:"))
async def on_day_chosen(callback: types.CallbackQuery, state: FSMContext):
    _, year, month, day = callback.data.split(":")
    selected = datetime(int(year), int(month), int(day)).date()
    if selected < now_local().date():
        await callback.answer("Эта дата уже прошла", show_alert=True)
        return

    data = await state.get_data()
    service = data["service"]
    slots = await db.get_available_slots(service, selected.isoformat(), now_local())
    if not slots:
        await callback.answer("На эту дату свободного времени нет", show_alert=True)
        await show_calendar(callback, service, selected.year, selected.month)
        return

    await callback.answer()
    await state.update_data(date=selected.isoformat())
    await state.set_state(Booking.choosing_time)

    # по 4 кнопки в ряд, чтобы список не растягивался на весь экран
    rows = [
        [btn(t, f"slot:{slot_id}") for slot_id, t in slots[i:i + 4]]
        for i in range(0, len(slots), 4)
    ]
    rows.append([btn("« К календарю", f"back_cal:{selected.year}:{selected.month}"),
                 btn("Отмена", "menu:main")])
    await show_or_edit(
        callback,
        f"Услуга: {service}\nДата: {selected.strftime('%d.%m.%Y')}\nВыберите время:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(Booking.choosing_time, F.data.startswith("back_cal:"))
async def on_back_to_calendar(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    _, year, month = callback.data.split(":")
    await state.set_state(Booking.choosing_date)
    data = await state.get_data()
    await show_calendar(callback, data["service"], int(year), int(month))


# ---------------------------------------------------------------- бронирование
@dp.callback_query(Booking.choosing_time, F.data.startswith("slot:"))
async def on_slot_chosen(callback: types.CallbackQuery, state: FSMContext):
    try:
        slot_id = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer()
        return

    booking = await db.book_slot(slot_id, callback.from_user.id, now_local())
    if booking is None:
        await callback.answer("Это время уже занято, выберите другое", show_alert=True)
        data = await state.get_data()
        selected = datetime.strptime(data["date"], "%Y-%m-%d")
        await state.set_state(Booking.choosing_date)
        await show_calendar(callback, data["service"], selected.year, selected.month)
        return

    await callback.answer("Готово!")
    await state.clear()
    await show_or_edit(
        callback,
        f"✅ Вы записаны!\n\nУслуга: {booking['service']}\n"
        f"Дата: {fmt_date(booking['date'])}\nВремя: {booking['time']}\n\nСпасибо!",
    )
    await notify_admin(
        "Новая запись",
        booking,
        callback.from_user,
    )
    await callback.message.answer("Главное меню:", reply_markup=main_menu_markup())


async def notify_admin(title: str, booking: dict, user: types.User) -> None:
    """Уведомление администратору. Ошибка отправки не ломает сценарий пользователя."""
    text = (
        f"<b>{html.escape(title)}</b>\n"
        f"Услуга: {html.escape(booking['service'])}\n"
        f"Дата: {fmt_date(booking['date'])}\n"
        f"Время: {booking['time']}\n"
        f"Клиент: <a href=\"tg://user?id={user.id}\">{html.escape(user.full_name)}</a>"
    )
    if user.username:
        text += f" (@{html.escape(user.username)})"
    try:
        await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
    except Exception:
        logger.exception("Не удалось отправить уведомление администратору %s", ADMIN_ID)


# ---------------------------------------------------------------- мои записи / отмена
async def send_last_bookings(event: types.Message | types.CallbackQuery, user_id: int) -> None:
    bookings = await db.get_last_bookings(user_id, limit=3)
    back = InlineKeyboardMarkup(inline_keyboard=[[btn("« В меню", "menu:main")]])
    if not bookings:
        await show_or_edit(event, "У вас нет записей.", back)
        return
    lines = ["Ваши последние записи:\n"]
    for service, day, slot_time, _ in bookings:
        lines.append(f"• {service} — {fmt_date(day)} в {slot_time}")
    await show_or_edit(event, "\n".join(lines), back)


async def send_cancel_bookings(event: types.Message | types.CallbackQuery, user_id: int) -> None:
    bookings = await db.get_upcoming_bookings(user_id, now_local())
    if not bookings:
        await show_or_edit(
            event, "У вас нет предстоящих записей.",
            InlineKeyboardMarkup(inline_keyboard=[[btn("« В меню", "menu:main")]]),
        )
        return
    rows = [
        [btn(f"❌ {service}, {fmt_date(day)} {slot_time}", f"bcancel:{booking_id}")]
        for service, day, slot_time, booking_id in bookings
    ]
    rows.append([btn("« В меню", "menu:main")])
    await show_or_edit(event, "Какую запись отменить?", InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "menu:my")
async def on_my(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await send_last_bookings(callback, callback.from_user.id)


@dp.callback_query(F.data == "menu:cancel")
async def on_cancel_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await send_cancel_bookings(callback, callback.from_user.id)


@dp.message(Command("my_bookings"))
async def cmd_my_bookings(message: types.Message):
    await send_last_bookings(message, message.from_user.id)


@dp.message(Command("cancel_bookings"))
async def cmd_cancel_bookings(message: types.Message):
    await send_cancel_bookings(message, message.from_user.id)


@dp.callback_query(F.data.startswith("bcancel:"))
async def on_booking_cancel(callback: types.CallbackQuery, state: FSMContext):
    try:
        booking_id = int(callback.data.split(":")[1])
    except ValueError:
        await callback.answer()
        return
    cancelled = await db.cancel_booking(booking_id, callback.from_user.id)
    if cancelled is None:
        await callback.answer("Запись не найдена", show_alert=True)
    else:
        await callback.answer("Запись отменена")
        await notify_admin("Запись отменена", cancelled, callback.from_user)
    await send_cancel_bookings(callback, callback.from_user.id)


# ---------------------------------------------------------------- служебное
@dp.callback_query(F.data == "ignore")
async def on_ignore(callback: types.CallbackQuery):
    await callback.answer()


@dp.callback_query()
async def on_stale_callback(callback: types.CallbackQuery, state: FSMContext):
    """Кнопки из старых сообщений (например, после перезапуска бота)."""
    await callback.answer("Это меню устарело, начните заново", show_alert=False)
    await show_main_menu(callback, state)


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
    await bot.set_my_commands([
        BotCommand(command="start", description="Записаться на услугу"),
        BotCommand(command="my_bookings", description="Мои записи"),
        BotCommand(command="cancel_bookings", description="Отменить запись"),
        BotCommand(command="help", description="Справка"),
    ])
    refresher = asyncio.create_task(slots_refresher())
    try:
        await dp.start_polling(bot)
    finally:
        refresher.cancel()


if __name__ == "__main__":
    asyncio.run(main())
