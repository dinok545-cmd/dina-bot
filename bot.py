import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.enums import ChatMemberStatus
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
TEACHER_LINK = os.getenv("TEACHER_LINK", "https://t.me/academia_onlinee")

# ID проверяющего — ему показываем заглушку, чтобы не тратить лимит на его тесты.
# Всем остальным (включая Дину и настоящих учеников) — реальные ответы ИИ.
STUB_AI_IDS = {328761045}

KIE_API_KEY = os.getenv("KIE_API_KEY", "")

# Канал и материалы для подписчиков
CHANNEL_USERNAME = "@akhmedovadina"
CHANNEL_LINK = "https://t.me/akhmedovadina"
MINECRAFT_MATERIALS_LINK = "https://dinok545-cmd.github.io/havehasmemory_minecraft/"
SINCE_FOR_GAME_LINK = "https://dinok545-cmd.github.io/games2/"

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "images"
SLOTS_FILE = BASE_DIR / "slots.json"
USERS_FILE = BASE_DIR / "users.json"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Короткая история диалога для ИИ (хранится в памяти процесса)
AI_HISTORY: dict[int, list[dict[str, str]]] = {}



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
        return (
            "Сейчас свободных мест на занятия нет 😔\n"
            "Можно предложить удобное время при записи или написать Дине напрямую."
        )
    lines = "\n".join(f"• {s}" for s in slots)
    return f"📚 Свободные места на занятия:\n\n{lines}"


# ============ ИИ через KIE.ai ============
def is_contact_question(question: str) -> bool:
    q = (question or "").lower().replace("ё", "е")
    contact_phrases = (
        "как связаться",
        "связаться с диной",
        "с ней связаться",
        "как написать дине",
        "как ей написать",
        "куда написать",
        "написать дине",
        "дай контакт",
        "дайте контакт",
        "ее контакт",
        "её контакт",
        "контакт дины",
        "контакт",
        "контакты",
        "телеграм",
        "telegram",
        "тг",
        "ник дины",
        "юзернейм",
        "username",
    )
    return any(phrase in q for phrase in contact_phrases)


def clean_ai_answer(answer: str) -> str:
    # В диалоге не нужно заново здороваться перед каждым сообщением.
    answer = (answer or "").strip()
    answer = re.sub(
        r"^(?:здравствуйте|привет|добрый день|добрый вечер|доброе утро)[!,.?:;\s—–-]*",
        "",
        answer,
        count=1,
        flags=re.IGNORECASE,
    ).lstrip()
    return answer


async def ask_kie(question: str, user_id: int | None = None) -> str:
    if not KIE_API_KEY:
        return "ИИ временно не настроен, попробуй позже."

    question = (question or "").strip()
    if not question:
        return "Напиши вопрос текстом 🙂"

    # Контакты не отдаём на усмотрение модели: всегда возвращаем реальную ссылку из настроек бота.
    if is_contact_question(question):
        return (
            "Связаться с Диной напрямую в Telegram можно здесь:\n"
            f"{TEACHER_LINK}"
        )

    try:
        import urllib.error
        import urllib.request

        system_prompt = (
            "Ты — персональный ИИ-ассистент преподавателя Дины Ахмедовой. "
            "Отвечай тепло, естественно, кратко и по делу. Не начинай каждый ответ с приветствия. "
            "Ниже находится ПОДТВЕРЖДЁННАЯ база знаний. Отвечай на фактические вопросы только по ней. "
            "Если нужного факта в базе нет — не придумывай и не делай предположений: скажи, что точной "
            f"информации нет, и предложи уточнить у Дины напрямую: {TEACHER_LINK}.\n\n"

            "БАЗА ЗНАНИЙ О ЗАНЯТИЯХ:\n"
            "1. Предметы и классы. Английский язык — 1–11 класс. "
            "Русский язык и математика — 1–4 класс.\n"

            "2. Как проходят занятия. Занятия проходят онлайн с использованием интерактивной доски "
            "и видеозвонка в Zoom. Дина демонстрирует экран и даёт ученику возможность управлять "
            "интерактивными материалами. Благодаря этому ученик активно взаимодействует с заданиями, "
            "а процесс обучения становится более вовлекающим и практическим.\n"

            "3. Продолжительность обычного занятия — 60 минут.\n"

            "4. Форматы и стоимость. Групповой формат — не более 4 человек, 1000 ₽ за 60 минут. "
            "Парный формат — 1300 ₽ за 60 минут. Индивидуальный формат — 1500 ₽ за 60 минут.\n"

            "5. Пробное занятие. Пробный урок бесплатный и длится 30 минут.\n"

            "6. ОГЭ и ЕГЭ. Дина готовит к ОГЭ и ЕГЭ только по английскому языку. "
            "Индивидуальное занятие по подготовке к ОГЭ или ЕГЭ стоит 2000 ₽ за 60 минут.\n"

            "7. Договор. Перед началом регулярных занятий заключается договор.\n"

            "8. Перенос, отмена и болезнь. О переносе или отмене необходимо предупредить заранее. "
            "При отмене или переносе менее чем за 4 часа до начала урока занятие подлежит полной оплате. "
            "Исключение — болезнь ученика при наличии справки от врача. Если справки нет, оплата за "
            "пропущенное занятие не возвращается.\n"

            "9. Пропуск группового занятия. Если ученик пропускает групповое занятие, Дина отправляет "
            "ему запись урока и материалы, которые были на занятии. При пропуске участника занятие для "
            "остальных членов группы сокращается на 20 минут.\n"

            "10. Домашние задания. Да, домашние задания являются неотъемлемой частью обучения. "
            "Они помогают ещё лучше закрепить пройденный материал и подготовиться к следующему занятию. "
            "При регулярном выполнении домашних заданий ученик быстрее достигает поставленной учебной цели.\n"

            "11. Свободные места. Актуальные свободные места пользователь смотрит через кнопку "
            "«📚 Свободные места на занятия». Стандартные занятия длятся 60 минут, поэтому интервалы "
            "в расписании должны быть часовыми, например 18:00–19:00.\n"

            "12. Запись. Для записи пользователь может нажать «✏️ Записаться на занятие», выбрать "
            "свободное время или предложить свой вариант. Для бесплатного пробного урока есть отдельная "
            "кнопка «🎓 Записаться на пробное занятие».\n"

            f"13. Прямой Telegram Дины: {TEACHER_LINK}. Если спрашивают, как связаться, куда написать, "
            "просят Telegram, ник или контакт Дины — всегда давай именно эту ссылку и никогда не придумывай "
            "другой username, номер телефона или почту.\n"

            f"14. Telegram-канал Дины: {CHANNEL_LINK}.\n"

            "15. Бесплатные материалы. В боте есть раздел «🎁 Полезные материалы». После проверки "
            "подписки на канал пользователю доступны два материала: "
            "«Minecraft — рабочие листы и игры» и «Since/For Memory Challenge».\n"

            "16. Сертификаты. Подтверждённой информации о выдаче сертификатов нет. Если спрашивают "
            "о сертификате, скажи, что точной информации нет, и предложи уточнить у Дины напрямую.\n\n"

            "СТРОГИЕ ПРАВИЛА:\n"
            "• Не придумывай цены, скидки, акции, сертификаты, форматы, возрастные ограничения, "
            "расписание, контакты и условия, которых нет в базе.\n"
            "• Не называй никакие другие @username, кроме реального контакта из базы.\n"
            "• Не обещай результат за конкретный срок.\n"
            "• Если пользователь задаёт короткий уточняющий вопрос, учитывай предыдущие реплики диалога.\n"
            "• Если пользователь пишет по-русски, отвечай по-русски.\n"
            "• Не повторяй приветствие перед каждым ответом.\n"
            "• Если вопрос можно точно закрыть данными из базы, сразу дай конкретный ответ без фразы "
            "«уточните у преподавателя»."
        )

        history = AI_HISTORY.get(user_id or 0, [])[-6:]
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": question})

        payload = {
            "messages": messages,
            "stream": False,
        }

        def call_kie() -> dict:
            request = urllib.request.Request(
                "https://api.kie.ai/gemini-2.5-flash/v1/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {KIE_API_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )

            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                error_body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"KIE API HTTP {exc.code}: {error_body}") from exc

        data = await asyncio.to_thread(call_kie)
        content = data["choices"][0]["message"]["content"]

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    parts.append(str(item["text"]))
            content = "\n".join(parts)

        answer = clean_ai_answer(str(content))
        if not answer:
            return "Не удалось сформировать ответ. Попробуй задать вопрос ещё раз."

        if user_id is not None:
            hist = AI_HISTORY.setdefault(user_id, [])
            hist.append({"role": "user", "content": question})
            hist.append({"role": "assistant", "content": answer})
            AI_HISTORY[user_id] = hist[-8:]

        return answer

    except Exception:
        logging.exception("KIE AI error")
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


class TrialBooking(StatesGroup):
    waiting_details = State()


class AskAI(StatesGroup):
    waiting_question = State()


class SlotsAdmin(StatesGroup):
    waiting_new_slot = State()


# ============ КЛАВИАТУРЫ ============
def main_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🌟 Отзывы", callback_data="reviews")
    kb.button(text="📚 Свободные места на занятия", callback_data="slots")
    kb.button(text="✏️ Записаться на занятие", callback_data="book")
    kb.button(text="🎓 Записаться на пробное занятие", callback_data="trial_book")
    kb.button(text="🤖 Спросить ассистента", callback_data="ask_ai")
    kb.button(text="🎁 Полезные материалы", callback_data="materials")
    kb.button(text="💬 Написать Дине", url=TEACHER_LINK)
    kb.adjust(1)
    return kb.as_markup()


def slots_cta_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Записаться на занятие", callback_data="book")
    kb.button(text="💬 Написать Дине", url=TEACHER_LINK)
    kb.adjust(1)
    return kb.as_markup()


def booking_kb(slots: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, slot in enumerate(slots):
        kb.button(text=f"✅ {slot}", callback_data=f"bookslot_{i}")
    kb.button(text="📝 Предложить другое время", callback_data="book_other")
    kb.button(text="💬 Написать Дине", url=TEACHER_LINK)
    kb.button(text="⬅️ Назад в меню", callback_data="book_back")
    kb.adjust(1)
    return kb.as_markup()



def materials_gate_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📣 Подписаться", url=CHANNEL_LINK)
    kb.button(text="✅ Проверить подписку", callback_data="check_subscription")
    kb.button(text="⬅️ Назад", callback_data="materials_back")
    kb.adjust(1)
    return kb.as_markup()


def materials_list_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🎮 Minecraft — рабочие листы и игры",
        url=MINECRAFT_MATERIALS_LINK,
    )
    kb.button(
        text="⏳ Since/For Memory Challenge",
        url=SINCE_FOR_GAME_LINK,
    )
    kb.button(text="⬅️ Назад", callback_data="materials_back")
    kb.adjust(1)
    return kb.as_markup()


async def is_channel_subscriber(user_id: int) -> bool:
    """Проверяет подписку на канал. Бот должен быть администратором канала."""
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in {
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        }
    except Exception:
        logging.exception("Subscription check error")
        return False


def slots_admin_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить место", callback_data="admin_add_slot")
    kb.button(text="➖ Удалить место", callback_data="admin_del_slot")
    kb.button(text="📋 Показать все", callback_data="admin_show_slots")
    kb.adjust(1)
    return kb.as_markup()


# ============ /start ============
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
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
        "Помогает школьникам не бояться английского, разбираться "
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


# ============ Полезные материалы для подписчиков ============
@dp.callback_query(F.data == "materials")
async def materials_start(callback: CallbackQuery):
    if await is_channel_subscriber(callback.from_user.id):
        await callback.message.answer(
            "🎉 Подписка подтверждена!\n\nВыбирай материал 👇",
            reply_markup=materials_list_kb(),
        )
    else:
        await callback.message.answer(
            "🎁 Материалы доступны подписчикам канала Дины 💛\n\n"
            "Подпишись на канал и нажми «Проверить подписку».",
            reply_markup=materials_gate_kb(),
        )
    await callback.answer()


@dp.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    if await is_channel_subscriber(callback.from_user.id):
        await callback.message.edit_text(
            "🎉 Подписка подтверждена!\n\nВыбирай материал 👇",
            reply_markup=materials_list_kb(),
        )
        await callback.answer("Подписка подтверждена ✅")
    else:
        await callback.answer(
            "Пока не вижу подписку. Подпишись на канал и попробуй ещё раз.",
            show_alert=True,
        )


@dp.callback_query(F.data == "materials_back")
async def materials_back(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выбери, что тебя интересует 👇",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


# ============ Свободные места на занятия ============
@dp.callback_query(F.data == "slots")
async def show_slots(callback: CallbackQuery):
    slots = load_slots()
    text = format_slots(slots)
    if slots:
        text += (
            "\n\nЭто актуальные свободные места. "
            "Чтобы забронировать одно из них, нажми «Записаться на занятие» 👇"
        )
    await callback.message.answer(text, reply_markup=slots_cta_kb())
    await callback.answer()


# ============ Запись на занятие ============
@dp.callback_query(F.data == "book")
async def book_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    slots = load_slots()
    if slots:
        text = (
            "✏️ Запись на занятие\n\n"
            "Выбери удобное свободное время кнопкой ниже. "
            "После выбора Дина получит заявку и свяжется с тобой для подтверждения 💛\n\n"
            "Если ни один вариант не подходит — нажми «Предложить другое время»."
        )
    else:
        text = (
            "✏️ Запись на занятие\n\n"
            "Сейчас готовых свободных мест нет, но можно предложить удобный день и время — "
            "Дина посмотрит расписание и свяжется с тобой 💛"
        )
    await callback.message.answer(text, reply_markup=booking_kb(slots))
    await callback.answer()


@dp.callback_query(F.data.startswith("bookslot_"))
async def book_select_slot(callback: CallbackQuery, state: FSMContext):
    slots = load_slots()
    try:
        idx = int(callback.data.split("_")[1])
    except (ValueError, IndexError):
        return await callback.answer("Не получилось выбрать время. Попробуй ещё раз.", show_alert=True)

    if not 0 <= idx < len(slots):
        await callback.answer("Это место уже недоступно. Открой список заново.", show_alert=True)
        return

    selected = slots[idx]
    user = callback.from_user
    username = f"@{user.username}" if user.username else "без username"
    await bot.send_message(
        ADMIN_ID,
        f"📩 Новая заявка на запись!\n\n"
        f"Имя: {user.full_name}\n"
        f"Телеграм: {username} (id: {user.id})\n"
        f"Выбрано свободное место: {selected}",
    )
    await callback.message.answer(
        f"Готово ✅ Ты выбрал(а): {selected}\n\n"
        "Заявка отправлена Дине. Она свяжется с тобой для подтверждения 🙌",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Написать Дине", url=TEACHER_LINK)]]
        ),
    )
    await state.clear()
    await callback.answer("Заявка отправлена ✅")


@dp.callback_query(F.data == "book_other")
async def book_other_time(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Напиши удобный день и время одним сообщением ✍️\n"
        "Например: «вторник после 18:00» или «31 августа в 16:30»."
    )
    await state.set_state(Booking.waiting_time)
    await callback.answer()


@dp.callback_query(F.data == "book_back")
async def book_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "Выбери, что тебя интересует 👇",
        reply_markup=main_menu_kb(),
    )
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
        f"Пользователь предложил время: {message.text}",
    )
    await message.answer(
        "Спасибо! Я передал(а) Дине твой вариант времени 💛\n"
        "Она посмотрит расписание и свяжется с тобой для подтверждения.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="💬 Написать Дине", url=TEACHER_LINK)]]
        ),
    )
    await state.clear()



# ============ Запись на бесплатное пробное занятие ============
@dp.callback_query(F.data == "trial_book")
async def trial_book_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "🎓 Бесплатное пробное занятие\n\n"
        "Пробный урок бесплатный, длится 30 минут и проходит онлайн.\n\n"
        "Чтобы записаться, напиши одним сообщением:\n"
        "• имя ученика;\n"
        "• класс;\n"
        "• предмет (английский / русский / математика);\n"
        "• удобный день и время.\n\n"
        "Например: «Маша, 6 класс, английский, вторник после 18:00»."
    )
    await state.set_state(TrialBooking.waiting_details)
    await callback.answer()


@dp.message(TrialBooking.waiting_details)
async def trial_book_receive(message: Message, state: FSMContext):
    user = message.from_user
    username = f"@{user.username}" if user.username else "без username"

    await bot.send_message(
        ADMIN_ID,
        "🎓 Новая заявка на БЕСПЛАТНОЕ пробное занятие!\n\n"
        f"Имя в Telegram: {user.full_name}\n"
        f"Телеграм: {username} (id: {user.id})\n"
        f"Данные от пользователя:\n{message.text}",
    )

    await message.answer(
        "Готово ✅ Заявка на бесплатное пробное занятие отправлена Дине.\n\n"
        "Пробный урок длится 30 минут. Дина свяжется с тобой, чтобы подтвердить время 💛",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💬 Написать Дине", url=TEACHER_LINK)]
            ]
        ),
    )
    await state.clear()


# ============ Спросить ассистента (ИИ) ============
@dp.callback_query(F.data == "ask_ai")
async def ask_ai_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Напиши свой вопрос, и ассистент постарается помочь 🤖\n\n"
        "Можно задавать несколько вопросов подряд. Чтобы выйти из режима ассистента — отправь /start."
    )
    await state.set_state(AskAI.waiting_question)
    await callback.answer()


@dp.message(AskAI.waiting_question)
async def ask_ai_receive(message: Message, state: FSMContext):
    if message.from_user.id in STUB_AI_IDS:
        await message.answer(STUB_ANSWER)
        return

    await message.chat.do("typing")
    answer = await ask_kie(message.text, message.from_user.id)
    await message.answer(answer)
    # Состояние не сбрасываем: можно продолжать диалог.


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
    await message.answer("Управление свободными местами:", reply_markup=slots_admin_kb())


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
        "Напиши новое свободное место текстом, например:\n<code>Пн 18:00 - 19:00</code>",
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
        await callback.message.answer("Список свободных мест пуст.")
        return await callback.answer()
    kb = InlineKeyboardBuilder()
    for i, s in enumerate(slots):
        kb.button(text=f"❌ {s}", callback_data=f"delslot_{i}")
    kb.adjust(1)
    await callback.message.answer("Нажми, чтобы удалить свободное место:", reply_markup=kb.as_markup())
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


# ============ Любое обычное текстовое сообщение → ИИ ============
# Если FSM-состояние потерялось после перезапуска контейнера, бот всё равно не молчит.
@dp.message(F.text)
async def ai_fallback(message: Message):
    if message.text.startswith("/"):
        return
    if message.from_user.id in STUB_AI_IDS:
        await message.answer(STUB_ANSWER)
        return

    await message.chat.do("typing")
    answer = await ask_kie(message.text, message.from_user.id)
    await message.answer(answer)


# ============ ЗАПУСК ============
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
