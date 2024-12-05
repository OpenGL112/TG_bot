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
API_TOKEN = "7062809410:AAFy6p7oSkNF11FDC7sa7fXmfCIrxbIlcBM"
ADMIN_ID = int("308099810")


bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# FSM для управления шагами
class Booking(StatesGroup):
    choosing_service = State()
    refining_service = State()
    choosing_date = State()
    choosing_time = State()

# Функция для генерации календаря
def generate_calendar(year: int, month: int) -> InlineKeyboardMarkup:
    calendar = monthcalendar(year, month)
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

    for week in calendar:
        week_buttons = []
        for day in week:
            if day == 0:
                week_buttons.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                week_buttons.append(InlineKeyboardButton(text=str(day), callback_data=f"date_{year}_{month}_{day}"))
        keyboard.append(week_buttons)

    navigation = [
        InlineKeyboardButton(text="<", callback_data=f"prev_{year}_{month}"),
        InlineKeyboardButton(text=">", callback_data=f"next_{year}_{month}"),
    ]
    keyboard.append(navigation)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# Генерация главного меню
async def show_start_menu(message_or_callback, state: FSMContext):
    services = ["Стрижка", "Окрашивание", "Укладка"]
    builder = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=service, callback_data=f"service_{service}")] for service in services
    ])
    builder.inline_keyboard.append([InlineKeyboardButton(text="Выход", callback_data="exit")])

    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer("Выберите услугу:", reply_markup=builder)
    elif isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.delete()
        await message_or_callback.message.answer("Выберите услугу:", reply_markup=builder)

    await state.set_state(Booking.choosing_service)


# Обработка выхода
@dp.callback_query(F.data == "exit")
async def handle_exit(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.answer("Сессия завершена. Возвращаемся в главное меню.")
    await state.clear()
    await show_start_menu(callback_query, state)


# Команда /start
@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await show_start_menu(message, state)


# Обработка выбора услуги
@dp.callback_query(F.data.startswith("service_"))
async def handle_service(callback_query: types.CallbackQuery, state: FSMContext):
    service = callback_query.data.split("_")[1]
    await state.update_data(service=service)

    if service == "Стрижка":
        options = ["Короткая", "Длинная", "Отмена"]
        builder = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=option, callback_data=f"refine_{option}")]
            for option in options
        ])
        await callback_query.message.edit_text("Выберите тип стрижки:", reply_markup=builder)
        await state.set_state(Booking.refining_service)
    else:
        today = datetime.now()
        calendar = generate_calendar(today.year, today.month)
        await callback_query.message.answer("Выберите дату:", reply_markup=calendar)
        await state.set_state(Booking.choosing_date)


# Уточнение услуги
@dp.callback_query(F.data.startswith("refine_"))
async def handle_refine_service(callback_query: types.CallbackQuery, state: FSMContext):
    refinement = callback_query.data.split("_")[1]

    if refinement == "Отмена":
        await show_start_menu(callback_query, state)
    else:
        await state.update_data(refinement=refinement)
        today = datetime.now()
        calendar = generate_calendar(today.year, today.month)
        await callback_query.message.answer("Выберите дату:", reply_markup=calendar)
        await state.set_state(Booking.choosing_date)


# Обработка выбора даты
@dp.callback_query(F.data.startswith("date_"))
async def handle_date(callback_query: types.CallbackQuery, state: FSMContext):
    _, year, month, day = callback_query.data.split("_")
    selected_date = f"{year}-{str(month).zfill(2)}-{str(day).zfill(2)}"
    await state.update_data(date=selected_date)

    data = await state.get_data()
    service = data["service"]

    # Получаем доступные слоты
    available_slots = await db.get_available_slots(service, selected_date)
    if not available_slots:
        await callback_query.message.answer("На выбранную дату нет доступных слотов. Выберите другую дату.")
        return

    # Генерация клавиатуры для выбора времени
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{time}", callback_data=f"slot_{slot_id}")]
        for slot_id, time in available_slots
    ])
    keyboard.inline_keyboard.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])

    await callback_query.message.answer("Выберите время:", reply_markup=keyboard)
    await state.set_state(Booking.choosing_time)


# Кнопка "Отмена"
@dp.callback_query(F.data == "cancel")
async def handle_cancel(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.answer("Действие отменено. Возвращаемся в меню.")
    await state.clear()
    await show_start_menu(callback_query, state)


# Обработка команды /help
@dp.message(Command("help"))
async def show_help(message: types.Message):
    help_text = (
        "/start - запуск бота для записи\n"
        "/help - описание доступных функций\n"
        "/my_bookings - возвращает последнее бронирование"
    )
    await message.answer(help_text)


# Команда /my_bookings
@dp.message(Command("my_bookings"))
async def my_bookings(message: types.Message):
    user_id = message.from_user.id

    last_bookings = await db.get_last_bookings(user_id, limit=3)

    if not last_bookings:
        await message.answer("У вас нет недавних бронирований.")
        return

    response = "Ваши последние бронирования:\n\n"
    for booking in last_bookings:
        # Обращаемся к элементам через индексы, если это кортеж
        response += (
            f"Услуга: {booking[0]}\n"  # service
            f"Дата: {booking[1]}\n"    # date
            f"Время: {booking[2]}\n\n" # time
        )

    await message.answer(response)


# Завершение бронирования
@dp.callback_query(F.data.startswith("slot_"))
async def handle_time_and_finish(callback_query: types.CallbackQuery, state: FSMContext):
    slot_id = int(callback_query.data.split("_")[1])
    user_id = callback_query.from_user.id  # Получаем user_id из callback_query
    await db.book_slot(slot_id, user_id)  # Вызываем функцию бронирования

    data = await state.get_data()
    service = data["service"]
    date = data["date"]

    # Ответ пользователю
    await callback_query.message.answer(
        f"Вы успешно записаны на услугу: {service}\nДата: {date}\nСпасибо!"
    )

    # Уведомление администратору
    await bot.send_message(
        ADMIN_ID,
        f"Новая запись:\nУслуга: {service}\nДата: {date}\nПользователь: {callback_query.from_user.full_name}"
    )

    await state.clear()
    await show_start_menu(callback_query, state)


# Инициализация базы данных
async def on_startup():
    await db.init_db()


# Запуск бота
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(on_startup())
    asyncio.run(main())