import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime
from calendar import monthcalendar, month_name
from dotenv import load_dotenv
import asyncio
import db

load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in the environment")

ADMIN_ID = int(os.getenv("ADMIN_ID", "308099810"))

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class Booking(StatesGroup):
    choosing_service = State()
    refining_service = State()
    choosing_date = State()
    choosing_time = State()
    main_menu = State()


def generate_calendar(year: int, month: int) -> InlineKeyboardMarkup:
    current_date = datetime.now().date()
    month_grid = monthcalendar(year, month)
    keyboard = []

    header_row = [
        InlineKeyboardButton(
            text=f"{month_name[month]} {year}",
            callback_data="ignore"
        )
    ]
    keyboard.append(header_row)

    header = [
        InlineKeyboardButton(text="Пн", callback_data="ignore"),
        InlineKeyboardButton(text="Вт", callback_data="ignore"),
        InlineKeyboardButton(text="Ср", callback_data="ignore"),
        InlineKeyboardButton(text="Чт", callback_data="ignore"),
        InlineKeyboardButton(text="Пт", callback_data="ignore"),
        InlineKeyboardButton(text="Сб", callback_data="ignore"),
        InlineKeyboardButton(text="Вс", callback_data="ignore"),
    ]
    keyboard.append(header)

    for week in month_grid:
        week_buttons = []
        for day in week:
            if day == 0:
                week_buttons.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                date_obj = datetime(year, month, day).date()
                if date_obj < current_date:
                    week_buttons.append(InlineKeyboardButton(text=f"🔒 {day}", callback_data="ignore"))
                else:
                    week_buttons.append(InlineKeyboardButton(text=str(day), callback_data=f"date_{year}_{month}_{day}"))
        keyboard.append(week_buttons)

    navigation = [
        InlineKeyboardButton(text="<", callback_data=f"prev_1_{year}_{month}"),
        InlineKeyboardButton(text=">", callback_data=f"next_1_{year}_{month}"),
    ]
    keyboard.append(navigation)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@dp.callback_query(F.data.startswith("next_1_"))
async def handle_next_1(callback_query: types.CallbackQuery):
    await callback_query.answer()
    _, _, year_s, month_s = callback_query.data.split("_")
    year, month = int(year_s), int(month_s)
    month += 1
    if month > 12:
        month = 1
        year += 1
    calendar = generate_calendar(year, month)
    await callback_query.message.edit_reply_markup(reply_markup=calendar)


@dp.callback_query(F.data.startswith("prev_1_"))
async def handle_prev_1(callback_query: types.CallbackQuery):
    await callback_query.answer()
    _, _, year_s, month_s = callback_query.data.split("_")
    year, month = int(year_s), int(month_s)
    month -= 1
    if month < 1:
        month = 12
        year -= 1
    calendar = generate_calendar(year, month)
    await callback_query.message.edit_reply_markup(reply_markup=calendar)


async def show_start_menu(message_or_callback, state: FSMContext):
    services = ["Услуги", "Мои записи", "Отменить запись"]
    builder = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=service, callback_data=f"service_{service}")] for service in services
    ])
    builder.inline_keyboard.append([InlineKeyboardButton(text="Ссылка", url="https://www.google.com")])
    builder.inline_keyboard.append([InlineKeyboardButton(text="Выход", callback_data="exit")])

    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer("Сервисы:", reply_markup=builder)
    elif isinstance(message_or_callback, types.CallbackQuery):
        try:
            await message_or_callback.message.delete()
        except Exception:
            pass
        await message_or_callback.message.answer("Сервисы:", reply_markup=builder)

    await state.set_state(Booking.choosing_service)


@dp.callback_query(F.data == "exit")
async def handle_exit(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await callback_query.message.answer("Сессия завершена. Возвращаемся в главное меню.")
    await state.clear()
    await show_start_menu(callback_query, state)


@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await show_start_menu(message, state)


@dp.callback_query(F.data.startswith("service_"))
async def handle_service(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    service = callback_query.data.split("_", maxsplit=1)[1]
    await state.update_data(service=service)

    if service == "Услуги":
        options = ["Стрижка", "Окрашивание", "Укладка", "Назад"]
        builder = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=option, callback_data=f"refine_{option}")]
            for option in options
        ])
        await callback_query.message.edit_text("Выберите услугу:", reply_markup=builder)
        await state.set_state(Booking.refining_service)
    elif service == "Мои записи":
        user_id = callback_query.from_user.id
        await send_last_bookings(callback_query.message, user_id)
    elif service == "Отменить запись":
        user_id = callback_query.from_user.id
        await send_cancel_bookings(callback_query.message, user_id)


@dp.callback_query(F.data.startswith("refine_"))
async def handle_refine_service(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    refinement = callback_query.data.split("_", maxsplit=1)[1]

    if refinement == "Назад":
        await show_start_menu(callback_query, state)
        return

    await state.update_data(refinement=refinement)
    today = datetime.now()
    calendar = generate_calendar(today.year, today.month)
    await callback_query.message.answer("Выберите дату:", reply_markup=calendar)
    await state.set_state(Booking.choosing_date)


@dp.callback_query(F.data.startswith("date_"))
async def handle_date(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    _, year, month, day = callback_query.data.split("_")
    selected_date = f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
    await state.update_data(date=selected_date)

    data = await state.get_data()
    service = data.get("refinement")
    if not service:
        await callback_query.message.answer("Не удалось определить услугу. Начните заново.")
        await state.clear()
        await show_start_menu(callback_query, state)
        return

    available_slots = await db.get_available_slots(service, selected_date)
    if not available_slots:
        await callback_query.message.answer("На выбранную дату нет доступных слотов. Выберите другую дату.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{time}", callback_data=f"slot_{slot_id}")]
        for slot_id, time in available_slots
    ])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])

    await callback_query.message.answer("Выберите время:", reply_markup=keyboard)
    await state.set_state(Booking.choosing_time)


@dp.callback_query(F.data == "cancel")
async def handle_cancel(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await callback_query.message.answer("Действие отменено. Возвращаемся в меню.")
    await state.clear()
    await show_start_menu(callback_query, state)


@dp.message(Command("help"))
async def show_help(message: types.Message):
    help_text = (
        "/start - запуск бота для записи\n"
        "/help - описание доступных функций\n"
        "/my_bookings - возвращает недавние бронирования\n"
        "/cancel_bookings - отмена активных бронирований\n"
    )
    await message.answer(help_text)


async def send_last_bookings(message: types.Message, user_id: int):
    last_bookings = await db.get_last_bookings(user_id, limit=3)
    if not last_bookings:
        await message.answer("У вас нет недавних бронирований.")
        return

    response = "Ваши бронирования:\n\n"
    for booking in last_bookings:
        response += (
            f"Услуга: {booking[0]}\n"
            f"Дата: {booking[1]}\n"
            f"Время: {booking[2]}\n\n"
        )

    await message.answer(response)


@dp.message(Command("my_bookings"))
async def my_bookings_cmd(message: types.Message):
    await send_last_bookings(message, message.from_user.id)


async def send_cancel_bookings(message: types.Message, user_id: int):
    today = datetime.today().date()
    last_bookings = await db.get_last_bookings(user_id, limit=3)
    if not last_bookings:
        await message.answer("У вас нет активных бронирований.")
        return

    booking_texts = []
    slot_ids = []
    for booking in last_bookings:
        booking_date = datetime.strptime(booking[1], '%Y-%m-%d').date()
        if booking_date >= today:
            booking_texts.append(
                f"Услуга: {booking[0]}\nДата: {booking[1]}\nВремя: {booking[2]}\n"
            )
            slot_ids.append(booking[3])

    if not booking_texts:
        await message.answer("У вас нет активных бронирований.")
        return

    await message.answer("Какое бронирование Вы хотите отменить?\n")
    for text, slot_id in zip(booking_texts, slot_ids):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Отмена', callback_data=f"db_cancel_booking_{slot_id}")]
        ])
        await message.answer(text=text, reply_markup=kb)


@dp.message(Command("cancel_bookings"))
async def cancel_bookings_cmd(message: types.Message):
    await send_cancel_bookings(message, message.from_user.id)


@dp.callback_query(F.data.startswith("db_cancel_booking_"))
async def handle_db_cancel_booking(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    slot_id = callback_query.data[len("db_cancel_booking_"):]
    user_id = callback_query.from_user.id
    final = await db.cancel_slot(slot_id, user_id)
    await callback_query.message.answer(final)
    await show_start_menu(callback_query, state)


@dp.callback_query(F.data.startswith("slot_"))
async def handle_time_and_finish(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.answer()
    slot_id = int(callback_query.data.split("_")[1])
    user_id = callback_query.from_user.id
    slot_time = await db.book_slot(slot_id, user_id)

    data = await state.get_data()
    service = data.get("refinement", "—")
    date = data.get("date", "—")

    await callback_query.message.answer(
        f"Вы успешно записаны на услугу: {service}\nДата: {date}\nВремя: {slot_time}\nСпасибо!"
    )

    link = f'<a href="tg://user?id={user_id}">Заказчик</a>'
    await bot.send_message(
        ADMIN_ID,
        f"Новая запись:\nУслуга: {service}\nДата: {date}\nВремя: {slot_time}\n"
        f"Пользователь: {callback_query.from_user.full_name}"
        f"\nСсылка на профиль: {link}",
        parse_mode="HTML"
    )

    await state.clear()
    await show_start_menu(callback_query, state)


async def main():
    await db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
