import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    InputMediaPhoto,
    TelegramObject,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "864712374"))  # кому приходят уведомления о записи
TEACHER_LINK = os.getenv("TEACHER_LINK", "https://t.me/english_dina_bot")

# ID проверяющего — ему показываем заглушку, чтобы не тратить лимит на его тесты.
# Всем остальным (включая Дину и настоящих учеников) — реальные ответы ИИ.
STUB_AI_IDS = {328761045}

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "images"
SLOTS_FILE = BASE_DIR / "slots.json"
USERS_FILE = BASE_DIR / "users.json"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ============ УЧЁТ ПОЛЬЗОВАТЕЛЕЙ ============
def load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_users(users: dict) -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def track_user(user) -> None:
    if user is None:
        return
    users = load_users()
    key = str(user.id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if key not in users:
        users[key] = {
            "name": user.full_name,
            "username": user.username,
            "first_seen": now,
            "last_seen": now,
            "messages": 1,
        }
    else:
        users[key]["last_seen"] = now
        users[key]["name"] = user.full_name
        users[key]["username"] = user.username
        users[key]["messages"] = users[key].get("messages", 0) + 1
    save_users(users)


class UserTrackerMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, "from_user", None)
        track_user(user)
        return await handler(event, data)


dp.message.middleware(UserTrackerMiddleware())
dp.callback_query.middleware(UserTrackerMiddleware())


# ============ ХРАНЕНИЕ ОКОШЕК (простой JSON-файл) ============
def load_slots() -> list[str]:
    if not SLOTS_FILE.exists():
        return []
    with open(SLOTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_slots(slots: list[str]) -> None:
    with open(SLOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(slots, f, ensure_ascii=False, indent=2)


def format_slots(slots: list[str]) -> str:
    if not slots:
        return "Пока нет свободных окошек 😔\nНапиши преподавателю напрямую, чтобы уточнить расписание."
    lines = "\n".join(f"• {s}" for s in slots)
    return f"📅 Свободные окошки:\n\n{lines}"


# ============ ИИ (Gemini) ============
async def ask_gemini(question: str) -> str:
    if not GEMINI_API_KEY:
        return "ИИ временно не настроен, попробуй позже."
    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            "Ты — дружелюбный помощник преподавателя английского языка и предметов "
            "начальной школы Дины Ахмедовой. Отвечай кратко, тепло и по делу на "
            "вопросы учеников и родителей об английском, учёбе и уроках.\n\n"
            f"Вопрос: {question}"
        )
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()
    except Exception as e:
        logging.exception("Gemini error")
        return "Не получилось получить ответ от ИИ, попробуй чуть позже 🙏"


STUB_ANSWER = (
    "🤖 Это демо-режим ассистента (чтобы не тратить бесплатный лимит запросов).\n\n"
    "Пример того, как отвечает ИИ:\n"
    "«Привет! Я помогу разобраться с английским — грамматикой, словами, произношением "
    "или просто отвечу на вопрос по школьной программе. Пиши, что интересует!»\n\n"
    "Полный доступ к живым ответам ИИ есть у автора проекта."
)


# ============ СОСТОЯНИЯ (FSM) ============
class Booking(StatesGroup):
    waiting_time = State()


class AskAI(StatesGroup):
    waiting_question = State()


class SlotsAdmin(StatesGroup):
    waiting_new_slot = State()


# ============ КЛАВИАТУРЫ ============
def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🌟 Отзывы", callback_data="reviews")
    kb.button(text="📅 Свободные окошки", callback_data="slots")
    kb.button(text="✏️ Записаться на урок", callback_data="book")
    kb.button(text="🤖 Спросить ассистента", callback_data="ask_ai")
    kb.adjust(1)
    return kb.as_markup()


def slots_admin_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить окошко", callback_data="admin_add_slot")
    kb.button(text="➖ Удалить окошко", callback_data="admin_del_slot")
    kb.button(text="📋 Показать все", callback_data="admin_show_slots")
    kb.adjust(1)
    return kb.as_markup()


# ============ /start ============
@dp.message(CommandStart())
async def cmd_start(message: Message):
    welcome_photo = IMAGES_DIR / "welcome.jpg"
    caption = "Приветики! 👋"
    if welcome_photo.exists():
        await message.answer_photo(FSInputFile(welcome_photo), caption=caption)
    else:
        await message.answer(caption)

    await message.answer(
        "Это персональный ассистент <b>Дины Ахмедовой</b>.",
        parse_mode="HTML",
    )
    await message.answer(
        "Дина — крутой специалист по английскому языку и предметам начальной школы. "
        "Уже много лет помогает детям и взрослым не бояться английского, разбираться "
        "со школьной программой и уверенно двигаться вперёд 🚀\n\n"
        "Выбери, что тебя интересует 👇",
        reply_markup=main_menu_kb(),
    )


# ============ Отзывы ============
@dp.callback_query(F.data == "reviews")
async def show_reviews(callback: CallbackQuery):
    photos = []
    for i in (1, 2, 3):
        path = IMAGES_DIR / f"review{i}.jpg"
        if path.exists():
            photos.append(InputMediaPhoto(media=FSInputFile(path)))
    if photos:
        await callback.message.answer_media_group(photos)
    else:
        await callback.message.answer(
            "Отзывы скоро появятся здесь 🙂 (админ ещё не загрузил картинки)"
        )
    await callback.answer()


# ============ Свободные окошки ============
@dp.callback_query(F.data == "slots")
async def show_slots(callback: CallbackQuery):
    slots = load_slots()
    await callback.message.answer(format_slots(slots))
    await callback.answer()


# ============ Запись на урок ============
@dp.callback_query(F.data == "book")
async def book_start(callback: CallbackQuery, state: FSMContext):
    slots = load_slots()
    text = (
        format_slots(slots)
        + f"\n\nНапиши, какой день и время тебе удобны ✍️\n\n"
        + f"Также можешь написать преподавателю напрямую: {TEACHER_LINK}"
    )
    await callback.message.answer(text)
    await state.set_state(Booking.waiting_time)
    await callback.answer()


@dp.message(Booking.waiting_time)
async def book_receive_time(message: Message, state: FSMContext):
    user = message.from_user
    username = f"@{user.username}" if user.username else "без username"
    await bot.send_message(
        ADMIN_ID,
        f"📩 Новая заявка на запись!\n\n"
        f"Имя: {user.full_name}\n"
        f"Телеграм: {username} (id: {user.id})\n"
        f"Желаемое время: {message.text}",
    )
    await message.answer(
        "Спасибо! Заявка отправлена преподавателю, с тобой скоро свяжутся 🙌"
    )
    await state.clear()


# ============ Спросить ассистента (ИИ) ============
@dp.callback_query(F.data == "ask_ai")
async def ask_ai_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Напиши свой вопрос, и ассистент постарается помочь 🤖")
    await state.set_state(AskAI.waiting_question)
    await callback.answer()


@dp.message(AskAI.waiting_question)
async def ask_ai_receive(message: Message, state: FSMContext):
    if message.from_user.id in STUB_AI_IDS:
        await message.answer(STUB_ANSWER)
    else:
        await message.chat.do("typing")
        answer = await ask_gemini(message.text)
        await message.answer(answer)
    await state.clear()


# ============ Админ: статистика пользователей /users ============
@dp.message(Command("users"))
async def show_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    users = load_users()
    if not users:
        await message.answer("Пока никто не писал боту.")
        return
    sorted_users = sorted(
        users.items(), key=lambda kv: kv[1]["last_seen"], reverse=True
    )
    lines = [f"👥 Всего пользователей: {len(users)}\n"]
    for uid, info in sorted_users[:30]:
        uname = f"@{info['username']}" if info.get("username") else "без username"
        lines.append(
            f"• {info['name']} ({uname}, id:{uid})\n"
            f"   последний раз: {info['last_seen']}, сообщений: {info.get('messages', 1)}"
        )
    text = "\n".join(lines)
    for i in range(0, len(text), 3500):
        await message.answer(text[i : i + 3500])


# ============ Админ: управление окошками /okna ============
@dp.message(Command("okna"))
async def okna_menu(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("Управление свободными окошками:", reply_markup=slots_admin_kb())


@dp.callback_query(F.data == "admin_show_slots")
async def admin_show_slots(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer()
    await callback.message.answer(format_slots(load_slots()))
    await callback.answer()


@dp.callback_query(F.data == "admin_add_slot")
async def admin_add_slot_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer()
    await callback.message.answer(
        "Напиши новое окошко текстом, например:\n<code>Пн 18:00 - 18:45</code>",
        parse_mode="HTML",
    )
    await state.set_state(SlotsAdmin.waiting_new_slot)
    await callback.answer()


@dp.message(SlotsAdmin.waiting_new_slot)
async def admin_add_slot_receive(message: Message, state: FSMContext):
    slots = load_slots()
    slots.append(message.text.strip())
    save_slots(slots)
    await message.answer(f"Добавлено ✅\n\n{format_slots(slots)}")
    await state.clear()


@dp.callback_query(F.data == "admin_del_slot")
async def admin_del_slot_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer()
    slots = load_slots()
    if not slots:
        await callback.message.answer("Список окошек пуст.")
        return await callback.answer()
    kb = InlineKeyboardBuilder()
    for i, s in enumerate(slots):
        kb.button(text=f"❌ {s}", callback_data=f"delslot_{i}")
    kb.adjust(1)
    await callback.message.answer("Нажми, чтобы удалить окошко:", reply_markup=kb.as_markup())
    await callback.answer()


@dp.callback_query(F.data.startswith("delslot_"))
async def admin_del_slot_do(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer()
    idx = int(callback.data.split("_")[1])
    slots = load_slots()
    if 0 <= idx < len(slots):
        removed = slots.pop(idx)
        save_slots(slots)
        await callback.message.answer(f"Удалено: {removed}")
    await callback.answer()


# ============ ЗАПУСК ============
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
