import asyncio
import json
import logging
import os
import random
import re
import shutil
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
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "864712374"))  # кому приходят уведомления о записи
TEACHER_LINK = os.getenv("TEACHER_LINK", "https://t.me/academia_onlinee")

# ID проверяющего — ему показываем заглушку, чтобы не тратить лимит на его тесты.
# Всем остальным (включая Дину и настоящих учеников) — реальные ответы ИИ.

KIE_API_KEY = os.getenv("KIE_API_KEY", "")

# Канал и материалы для подписчиков
CHANNEL_USERNAME = "@akhmedovadina"
CHANNEL_LINK = "https://t.me/akhmedovadina"
MINECRAFT_MATERIALS_LINK = "https://dinok545-cmd.github.io/havehasmemory_minecraft/"
SINCE_FOR_GAME_LINK = "https://dinok545-cmd.github.io/games2/"
KOSMOMATIKA_LINK = "https://dinok545-cmd.github.io/kosmomatika/"

BASE_DIR = Path(__file__).parent
IMAGES_DIR = BASE_DIR / "images"

# Bothost создаёт DATA_DIR=/app/data. Там расписание и статистика переживают деплои.
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)

SLOTS_FILE = DATA_DIR / "slots.json"
USERS_FILE = DATA_DIR / "users.json"

# Пользователей можно перенести из старого файла.
# Расписание НЕ копируем из репозитория: раньше там лежали старые 45-минутные интервалы,
# из-за чего они возвращались после нового деплоя.
_legacy_users = BASE_DIR / "users.json"
if not USERS_FILE.exists() and _legacy_users.exists() and _legacy_users != USERS_FILE:
    try:
        shutil.copy2(_legacy_users, USERS_FILE)
    except Exception:
        logging.exception("Не удалось перенести users.json в DATA_DIR")

# Актуальное расписание на момент этой версии.
DEFAULT_SLOTS = [
    "Понедельник 16:00–17:00",
    "Среда 17:00–18:00",
    "Пятница 17:00–18:00",
]

# Старое расписание, которое ошибочно возвращалось после деплоя.
LEGACY_SLOTS_SIGNATURES = {
    "пн18:00-18:45",
    "ср17:00-17:45",
    "пт16:00-16:45",
}

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Короткая история диалога для ИИ (хранится в памяти процесса)
AI_HISTORY: dict[int, list[dict[str, str]]] = {}





# Квиз по неправильным глаголам английского языка.
IRREGULAR_VERB_QUIZ = [
    {"q": "go — went — ...?", "options": ["gone", "goed", "going"], "answer": 0, "explain": "go — went — gone"},
    {"q": "see — saw — ...?", "options": ["seen", "seed", "saw"], "answer": 0, "explain": "see — saw — seen"},
    {"q": "take — took — ...?", "options": ["taken", "taked", "took"], "answer": 0, "explain": "take — took — taken"},
    {"q": "write — wrote — ...?", "options": ["written", "writed", "wrote"], "answer": 0, "explain": "write — wrote — written"},
    {"q": "eat — ate — ...?", "options": ["eaten", "ated", "eat"], "answer": 0, "explain": "eat — ate — eaten"},
    {"q": "speak — spoke — ...?", "options": ["spoken", "speaked", "spoke"], "answer": 0, "explain": "speak — spoke — spoken"},
    {"q": "give — gave — ...?", "options": ["given", "gived", "gave"], "answer": 0, "explain": "give — gave — given"},
    {"q": "know — knew — ...?", "options": ["known", "knowed", "knew"], "answer": 0, "explain": "know — knew — known"},
    {"q": "begin — began — ...?", "options": ["begun", "begined", "began"], "answer": 0, "explain": "begin — began — begun"},
    {"q": "drink — drank — ...?", "options": ["drunk", "drinked", "drank"], "answer": 0, "explain": "drink — drank — drunk"},
    {"q": "buy — ... — bought", "options": ["bought", "buyed", "brought"], "answer": 0, "explain": "buy — bought — bought"},
    {"q": "think — ... — thought", "options": ["thought", "thinked", "taught"], "answer": 0, "explain": "think — thought — thought"},
    {"q": "find — ... — found", "options": ["found", "finded", "founded"], "answer": 0, "explain": "find — found — found"},
    {"q": "make — ... — made", "options": ["made", "maked", "make"], "answer": 0, "explain": "make — made — made"},
    {"q": "come — came — ...?", "options": ["come", "comed", "came"], "answer": 0, "explain": "come — came — come"},
    {"q": "run — ran — ...?", "options": ["run", "runned", "ran"], "answer": 0, "explain": "run — ran — run"},
]

# Мини-квиз по русскому языку для 1–4 классов.
RU_QUIZ = [
    {
        "q": "Какое слово написано правильно?",
        "options": ["машына", "машина", "мошина"],
        "answer": 1,
        "explain": "В сочетании ШИ пишем букву И: машина.",
    },
    {
        "q": "Какое слово написано правильно?",
        "options": ["чяща", "чаща", "чашаа"],
        "answer": 1,
        "explain": "ЧА–ЩА пишем с буквой А: чаща.",
    },
    {
        "q": "Какое слово написано правильно?",
        "options": ["чюдо", "чудо", "чюддо"],
        "answer": 1,
        "explain": "ЧУ–ЩУ пишем с буквой У: чудо.",
    },
    {
        "q": "В каком слове нужен мягкий знак?",
        "options": ["кон", "конь", "кони"],
        "answer": 1,
        "explain": "Слово «конь» оканчивается на мягкий согласный, поэтому пишем Ь.",
    },
    {
        "q": "Выбери проверочное слово к слову «дуб».",
        "options": ["дубы", "дубок", "дерево"],
        "answer": 0,
        "explain": "В слове «дубы» звук Б слышится ясно — это проверочное слово.",
    },
    {
        "q": "Где нужен разделительный мягкий знак?",
        "options": ["семя", "семья", "сима"],
        "answer": 1,
        "explain": "В слове «семья» перед Я после согласной пишется разделительный Ь.",
    },
    {
        "q": "Как правильно начать предложение?",
        "options": ["с большой буквы", "с маленькой буквы", "с цифры"],
        "answer": 0,
        "explain": "Предложение начинаем с большой буквы.",
    },
    {
        "q": "Какой знак чаще всего ставят в конце обычного сообщения-предложения?",
        "options": ["точку", "двоеточие", "скобку"],
        "answer": 0,
        "explain": "В конце повествовательного предложения обычно ставят точку.",
    },
    {
        "q": "Какое слово — имя существительное?",
        "options": ["бежать", "красивый", "книга"],
        "answer": 2,
        "explain": "«Книга» отвечает на вопрос «что?» — это имя существительное.",
    },
    {
        "q": "Какое слово — глагол?",
        "options": ["читать", "книга", "весёлый"],
        "answer": 0,
        "explain": "«Читать» обозначает действие и отвечает на вопрос «что делать?»",
    },
]

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
def _slot_signature(value: str) -> str:
    value = (value or "").lower().strip()
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"\\s+", "", value)
    return value


def load_slots() -> list[str]:
    if not SLOTS_FILE.exists():
        save_slots(DEFAULT_SLOTS.copy())
        return DEFAULT_SLOTS.copy()

    try:
        with open(SLOTS_FILE, "r", encoding="utf-8") as f:
            slots = json.load(f)
    except (json.JSONDecodeError, OSError):
        logging.exception("Не удалось прочитать slots.json")
        return []

    # Одноразовая автоматическая миграция именно старого ошибочного набора.
    signatures = {_slot_signature(s) for s in slots}
    if signatures == LEGACY_SLOTS_SIGNATURES:
        slots = DEFAULT_SLOTS.copy()
        save_slots(slots)
        logging.info("Старое 45-минутное расписание автоматически заменено на актуальное.")
    return slots


def save_slots(slots: list[str]) -> None:
    with open(SLOTS_FILE, "w", encoding="utf-8") as f:
        json.dump(slots, f, ensure_ascii=False, indent=2)


def normalize_slot_text(value: str) -> str | None:
    """
    Приводит расписание к единому виду: «Среда 17:00–18:00».
    Возвращает None, если формат не распознан или длительность не 60 минут.
    """
    raw = " ".join((value or "").strip().split())
    match = re.match(
        r"^(?P<day>.+?)\s+(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*[-–—]\s*"
        r"(?P<h2>\d{1,2}):(?P<m2>\d{2})$",
        raw,
    )
    if not match:
        return None

    h1, m1 = int(match["h1"]), int(match["m1"])
    h2, m2 = int(match["h2"]), int(match["m2"])
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59):
        return None

    start = h1 * 60 + m1
    end = h2 * 60 + m2
    if end - start != 60:
        return None

    day = match["day"].strip()
    if day:
        day = day[0].upper() + day[1:]

    return f"{day} {h1:02d}:{m1:02d}–{h2:02d}:{m2:02d}"


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
    """
    Финальная очистка ответа ИИ перед отправкой в Telegram.
    Markdown пользователю не показываем вообще: никаких звёздочек и решёток.
    """
    answer = (answer or "").strip()

    # Убираем повторное приветствие в начале ответа.
    answer = re.sub(
        r"^(?:здравствуйте|привет|добрый день|добрый вечер|доброе утро)[!,.?:;\\s—–-]*",
        "",
        answer,
        count=1,
        flags=re.IGNORECASE,
    ).lstrip()

    # Полностью убираем Markdown-символы, которые Telegram показывал как обычный текст.
    answer = answer.replace("*", "")
    answer = answer.replace("#", "")

    # Заодно убираем обратные кавычки Markdown, чтобы не появлялся `такой` текст.
    answer = answer.replace("`", "")

    # Убираем лишние пробелы перед переносами и слишком много пустых строк.
    answer = re.sub(r"[ \\t]+\\n", "\\n", answer)
    answer = re.sub(r"\\n{3,}", "\\n\\n", answer)

    return answer.strip()


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

            "15. Бесплатные материалы и тренажёры. В боте есть раздел «🎮 Тренажёры». После проверки "
            "подписки на канал пользователю доступны три материала: "
            "«Космоматика — тренажёр по математике для 1–4 классов», "
            "«Minecraft English — рабочие листы и игры» и «Since/For Memory Challenge».\n"

            "16. Сертификаты. Подтверждённой информации о выдаче сертификатов нет. Если спрашивают "
            "о сертификате, скажи, что точной информации нет, и предложи уточнить у Дины напрямую.\n\n"

            "КАК ОТВЕЧАТЬ НА НЕСТАНДАРТНЫЕ ВОПРОСЫ:\n"
            "• Ты не справочник, а живой дружелюбный ассистент. На бытовые, шуточные, учебные и общие "
            "вопросы можешь отвечать самостоятельно, использовать здравый смысл, лёгкий юмор и полезные советы.\n"
            "• Очень важно различать ОБЩИЕ знания и ФАКТЫ О ДИНЕ. Общие знания можно формулировать свободно. "
            "Факты о правилах, услугах, ценах, расписании, возможностях и решениях Дины — только из базы.\n"
            "• Если вопрос касается Дины, но точного факта в базе нет, не отвечай сухо «информации нет». "
            "Сначала естественно отреагируй на сам вопрос, а затем скажи, что конкретно этот момент лучше "
            f"уточнить у Дины лично: {TEACHER_LINK}.\n"
            "• Пример. Вопрос: «Можно прийти с котом?» Хороший ответ: "
            "«Ахах, классный вопрос 😄 Коты иногда становятся неотъемлемой частью онлайн-обучения! "
            f"Но можно ли устроить именно такой формат у Дины — лучше спросить её лично: {TEACHER_LINK}»\n"
            "• Пример. Вопрос: «Как быстрее выучить слова?» — дай полноценный полезный ответ из общих знаний, "
            "а не отправляй человека к Дине только потому, что этого вопроса нет в базе.\n\n"

            "СТРОГИЕ ПРАВИЛА:\n"
            "• Не придумывай цены, скидки, акции, сертификаты, форматы, возрастные ограничения, "
            "расписание, контакты, правила и условия Дины, которых нет в базе.\n"
            "• Не называй никакие другие @username, кроме реального контакта из базы.\n"
            "• Не обещай результат за конкретный срок.\n"
            "• Если пользователь задаёт короткий уточняющий вопрос, учитывай предыдущие реплики диалога.\n"
            "• Если пользователь пишет по-русски, отвечай по-русски.\n"
            "• Не повторяй приветствие перед каждым ответом.\n"
            "• Никогда не используй Markdown-разметку в ответах: не ставь звёздочки, решётки и обратные кавычки. "
            "Для структуры используй обычный текст, абзацы, нумерацию и эмодзи.\n"
            "• Не вставляй ссылку на Дину автоматически в каждый ответ. Она нужна только тогда, когда "
            "пользователь просит контакт или спрашивает о неподтверждённом факте именно о занятиях/правилах Дины.\n"
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
    """Компактное главное меню."""
    kb = InlineKeyboardBuilder()
    kb.button(text="🎓 Бесплатный пробный урок", callback_data="trial_book")
    kb.button(text="📚 Занятия", callback_data="lessons_menu")
    kb.button(text="🎮 Тренажёры", callback_data="materials")
    kb.button(text="⭐ Отзывы", callback_data="reviews")
    kb.button(text="🤖 Ассистент", callback_data="ask_ai")
    kb.button(text="💬 Написать Дине", url=TEACHER_LINK)
    kb.adjust(1, 2, 2, 1)
    return kb.as_markup()


def lessons_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📚 Свободные места", callback_data="slots")
    kb.button(text="✏️ Записаться", callback_data="book")
    kb.button(text="💰 Форматы и цены", callback_data="prices")
    kb.button(text="📋 Правила занятий", callback_data="rules")
    kb.button(text="⬅️ Главное меню", callback_data="main_back")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def slots_cta_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Записаться", callback_data="book")
    kb.button(text="💬 Написать Дине", url=TEACHER_LINK)
    kb.button(text="⬅️ К занятиям", callback_data="lessons_menu")
    kb.adjust(2, 1)
    return kb.as_markup()


def booking_kb(slots: list[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, slot in enumerate(slots):
        kb.button(text=f"✅ {slot}", callback_data=f"bookslot_{i}")
    kb.button(text="📝 Предложить другое время", callback_data="book_other")
    kb.button(text="💬 Написать Дине", url=TEACHER_LINK)
    kb.button(text="⬅️ К занятиям", callback_data="lessons_menu")
    kb.adjust(1)
    return kb.as_markup()


def materials_gate_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📣 Подписаться", url=CHANNEL_LINK)
    kb.button(text="✅ Проверить подписку", callback_data="check_subscription")
    kb.button(text="⬅️ Главное меню", callback_data="main_back")
    kb.adjust(1)
    return kb.as_markup()


def materials_list_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="🚀 Космоматика — тренажёр по математике 1–4 класс",
        url=KOSMOMATIKA_LINK,
    )
    kb.button(
        text="⛏ Minecraft English — рабочие листы и игры",
        url=MINECRAFT_MATERIALS_LINK,
    )
    kb.button(
        text="⏳ Since/For Memory Challenge",
        url=SINCE_FOR_GAME_LINK,
    )
    kb.button(text="⬅️ Главное меню", callback_data="main_back")
    kb.adjust(1)
    return kb.as_markup()


def assistant_quick_kb() -> InlineKeyboardMarkup:
    """Мини-хаб ассистента: вопрос + полезные игровые режимы."""
    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Задать вопрос", callback_data="ask_custom")
    kb.button(text="🇬🇧 Mini English", callback_data="mini_english")
    kb.button(text="🇷🇺 Квиз по русскому", callback_data="ru_quiz")
    kb.button(text="🔤 Неправильные глаголы", callback_data="irregular_quiz")
    kb.button(text="🎲 Сюрприз-задание", callback_data="assistant_surprise")
    kb.button(text="❓ Частые вопросы", callback_data="assistant_faq")
    kb.button(text="⬅️ Главное меню", callback_data="main_back")
    kb.adjust(1, 2, 2, 1, 1)
    return kb.as_markup()


def assistant_faq_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🖥 Как проходят занятия?", callback_data="faq_how")
    kb.button(text="💰 Сколько стоит?", callback_data="faq_price")
    kb.button(text="🎓 Пробный урок", callback_data="faq_trial")
    kb.button(text="🔄 Перенос и отмена", callback_data="faq_cancel")
    kb.button(text="⬅️ К ассистенту", callback_data="ask_ai")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def english_level_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🌱 A1", callback_data="mini_en_A1")
    kb.button(text="🌿 A2", callback_data="mini_en_A2")
    kb.button(text="🌳 B1", callback_data="mini_en_B1")
    kb.button(text="⬅️ К ассистенту", callback_data="ask_ai")
    kb.adjust(3, 1)
    return kb.as_markup()


def ru_quiz_kb(question_index: int) -> InlineKeyboardMarkup:
    q = RU_QUIZ[question_index]
    kb = InlineKeyboardBuilder()
    letters = ["А", "Б", "В"]
    for idx, option in enumerate(q["options"]):
        kb.button(
            text=f"{letters[idx]}. {option}",
            callback_data=f"ruans_{question_index}_{idx}",
        )
    kb.button(text="🎲 Другой вопрос", callback_data="ru_quiz")
    kb.button(text="⬅️ К ассистенту", callback_data="ask_ai")
    kb.adjust(1)
    return kb.as_markup()


def irregular_quiz_kb(question_index: int) -> InlineKeyboardMarkup:
    q = IRREGULAR_VERB_QUIZ[question_index]
    kb = InlineKeyboardBuilder()
    letters = ["A", "B", "C"]
    for idx, option in enumerate(q["options"]):
        kb.button(
            text=f"{letters[idx]}. {option}",
            callback_data=f"irans_{question_index}_{idx}",
        )
    kb.button(text="🎲 Другой глагол", callback_data="irregular_quiz")
    kb.button(text="⬅️ К ассистенту", callback_data="ask_ai")
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
        "Дина более 7 лет помогает школьникам <b>1–11 классов</b> с английским языком, "
        "а ученикам <b>1–4 классов</b> — ещё с русским и математикой.\n"
        "Интерактивные онлайн-занятия, индивидуальный подход и обучение без скучных уроков ✨\n\n"
        "Выбери нужный раздел 👇",
        parse_mode="HTML",
        reply_markup=main_menu_kb(),
    )


# ============ Навигация по меню ============
@dp.callback_query(F.data == "main_back")
async def main_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Главное меню 👇", reply_markup=main_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "lessons_menu")
async def lessons_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "📚 Занятия\n\nВыбери нужный раздел 👇",
        reply_markup=lessons_menu_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "prices")
async def show_prices(callback: CallbackQuery):
    await callback.message.answer(
        "💰 <b>Форматы и стоимость</b>\n\n"
        "👥 Группа до 4 человек — <b>1000 ₽ / 60 минут</b>\n"
        "👫 В паре — <b>1300 ₽ / 60 минут</b>\n"
        "👤 Индивидуально — <b>1500 ₽ / 60 минут</b>\n\n"
        "🎓 Подготовка к ОГЭ/ЕГЭ по английскому индивидуально — "
        "<b>2000 ₽ / 60 минут</b>\n\n"
        "Пробное занятие — <b>бесплатно, 30 минут</b>.",
        parse_mode="HTML",
        reply_markup=lessons_menu_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "rules")
async def show_rules(callback: CallbackQuery):
    await callback.message.answer(
        "📋 <b>Правила зависят от формата занятий:</b>\n\n"
        "📚 <b>Групповое занятие</b>\n"
        "Дина отправляет запись урока и все материалы. При пропуске одного ученика "
        "занятие для остальных участников сокращается на 20 минут.\n\n"
        "🔄 <b>Отмена или перенос</b>\n"
        "О переносе или отмене необходимо предупредить заранее. Если отмена или перенос "
        "происходят менее чем за 4 часа до занятия, урок подлежит полной оплате.\n\n"
        "🤒 <b>Болезнь</b>\n"
        "Исключение — болезнь ученика при наличии справки от врача. Без справки оплата "
        "за пропущенное занятие не возвращается.\n\n"
        "📝 Перед началом регулярных занятий заключается договор.",
        parse_mode="HTML",
        reply_markup=lessons_menu_kb(),
    )
    await callback.answer()


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
            "🎉 Подписка подтверждена!\n\nВыбирай тренажёр или материал 👇",
            reply_markup=materials_list_kb(),
        )
    else:
        await callback.message.answer(
            "🎮 Тренажёры доступны подписчикам канала Дины 💛\n\n"
            "Подпишись на канал и нажми «Проверить подписку».",
            reply_markup=materials_gate_kb(),
        )
    await callback.answer()


@dp.callback_query(F.data == "check_subscription")
async def check_subscription(callback: CallbackQuery):
    if await is_channel_subscriber(callback.from_user.id):
        await callback.message.edit_text(
            "🎉 Подписка подтверждена!\n\nВыбирай тренажёр или материал 👇",
            reply_markup=materials_list_kb(),
        )
        await callback.answer("Подписка подтверждена ✅")
    else:
        await callback.answer(
            "Пока не вижу подписку. Подпишись на канал и попробуй ещё раз.",
            show_alert=True,
        )




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
        "Выбери нужный раздел 👇",
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
    await state.clear()
    await callback.message.answer(
        "🤖 Ассистент Дины\n\n"
        "Здесь можно не только задать вопрос, но и немного потренироваться 👇",
        reply_markup=assistant_quick_kb(),
    )
    await callback.answer()



@dp.callback_query(F.data == "assistant_faq")
async def assistant_faq(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "❓ Частые вопросы о занятиях 👇",
        reply_markup=assistant_faq_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data == "mini_english")
async def mini_english(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "🇬🇧 Mini English\n\n"
        "Выбери уровень — ассистент создаст новый короткий текст и 3 вопроса к нему. "
        "Потом можешь прислать ответы прямо сюда, и он их проверит 👇",
        reply_markup=english_level_kb(),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("mini_en_"))
async def mini_english_generate(callback: CallbackQuery, state: FSMContext):
    level = callback.data.split("_")[-1]
    await callback.answer("Создаю мини-текст ✨")
    await callback.message.chat.do("typing")

    prompt = (
        f"Создай мини-задание по английскому уровня {level}. "
        "Нужен интересный мини-текст на 70–110 слов на бытовую или подростковую тему. "
        "После текста дай ровно 3 вопроса на понимание. "
        "Не давай ответы сразу. Не используй Markdown, звёздочки или решётки. "
        "В конце напиши одной строкой: «Пришли ответы 1–3, и я проверю 👀»."
    )
    answer = await ask_kie(prompt, callback.from_user.id)
    await callback.message.answer(
        "🇬🇧 Mini English\n\n" + answer,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Ещё текст", callback_data="mini_english")],
                [InlineKeyboardButton(text="⬅️ К ассистенту", callback_data="ask_ai")],
            ]
        ),
    )
    # Следующее текстовое сообщение пользователя попадёт в ИИ;
    # история уже содержит сам текст и вопросы, поэтому ИИ сможет проверить ответы.
    await state.set_state(AskAI.waiting_question)


@dp.callback_query(F.data == "ru_quiz")
async def ru_quiz(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    idx = random.randrange(len(RU_QUIZ))
    q = RU_QUIZ[idx]
    await callback.message.answer(
        "🇷🇺 Русский квиз · 1–4 класс\n\n"
        f"{q['q']}",
        reply_markup=ru_quiz_kb(idx),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("ruans_"))
async def ru_quiz_answer(callback: CallbackQuery):
    try:
        _, qidx_raw, answer_raw = callback.data.split("_")
        qidx = int(qidx_raw)
        chosen = int(answer_raw)
        q = RU_QUIZ[qidx]
    except (ValueError, IndexError, KeyError):
        return await callback.answer("Не получилось проверить ответ.", show_alert=True)

    if chosen == q["answer"]:
        result = "✅ Верно!"
    else:
        correct = ["А", "Б", "В"][q["answer"]]
        result = f"❌ Почти! Правильный ответ — {correct}."

    await callback.message.answer(
        f"{result}\n\n{q['explain']}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Следующий вопрос", callback_data="ru_quiz")],
                [InlineKeyboardButton(text="⬅️ К ассистенту", callback_data="ask_ai")],
            ]
        ),
    )
    await callback.answer()





@dp.callback_query(F.data == "irregular_quiz")
async def irregular_quiz(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    idx = random.randrange(len(IRREGULAR_VERB_QUIZ))
    q = IRREGULAR_VERB_QUIZ[idx]
    await callback.message.answer(
        "🔤 Квиз: неправильные глаголы\n\n"
        f"Выбери правильный вариант:\n\n{q['q']}",
        reply_markup=irregular_quiz_kb(idx),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("irans_"))
async def irregular_quiz_answer(callback: CallbackQuery):
    try:
        _, qidx_raw, answer_raw = callback.data.split("_")
        qidx = int(qidx_raw)
        chosen = int(answer_raw)
        q = IRREGULAR_VERB_QUIZ[qidx]
    except (ValueError, IndexError, KeyError):
        return await callback.answer("Не получилось проверить ответ.", show_alert=True)

    if chosen == q["answer"]:
        result = "✅ Верно!"
    else:
        correct = ["A", "B", "C"][q["answer"]]
        result = f"❌ Почти! Правильный вариант — {correct}."

    await callback.message.answer(
        f"{result}\n\n{q['explain']}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Следующий глагол", callback_data="irregular_quiz")],
                [InlineKeyboardButton(text="⬅️ К ассистенту", callback_data="ask_ai")],
            ]
        ),
    )
    await callback.answer()


@dp.callback_query(F.data == "assistant_surprise")
async def assistant_surprise(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    challenges = [
        (
            "английский",
            "Придумай короткое игровое задание по английскому на 1–2 минуты для школьника. "
            "Можно загадку, мини-диалог, выбор правильного варианта или словесный челлендж. "
            "Не давай ответ сразу. В конце предложи пользователю написать свой ответ."
        ),
        (
            "русский язык",
            "Придумай одно необычное короткое задание по русскому языку для ученика 1–4 класса. "
            "Оно должно занимать 1–2 минуты и иметь однозначный ответ. Не раскрывай ответ сразу. "
            "В конце предложи пользователю прислать ответ."
        ),
        (
            "математика",
            "Придумай одну весёлую математическую задачку для ученика 1–4 класса на 1–2 минуты. "
            "Без слишком больших чисел. Не раскрывай решение и ответ сразу. "
            "В конце предложи пользователю прислать ответ."
        ),
    ]
    subject, task = random.choice(challenges)
    await callback.answer("Выбираю задание 🎲")
    await callback.message.chat.do("typing")
    answer = await ask_kie(
        f"Сейчас ты создаёшь сюрприз-задание по теме: {subject}. {task} "
        "Не используй Markdown, звёздочки и решётки.",
        callback.from_user.id,
    )
    await callback.message.answer(
        "🎲 Задание-сюрприз\n\n" + answer,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎲 Другое задание", callback_data="assistant_surprise")],
                [InlineKeyboardButton(text="⬅️ К ассистенту", callback_data="ask_ai")],
            ]
        ),
    )
    await state.set_state(AskAI.waiting_question)


@dp.message(Command("play"))
async def play_command(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🎮 Мини-режимы ассистента\n\n"
        "Выбирай: английский мини-текст, квиз по русскому, неправильные глаголы или сюрприз-задание 👇",
        reply_markup=assistant_quick_kb(),
    )


@dp.callback_query(F.data == "ask_custom")
async def ask_custom(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Напиши свой вопрос одним сообщением 🤖\n\n"
        "Можно задавать несколько вопросов подряд. Чтобы выйти — отправь /start."
    )
    await state.set_state(AskAI.waiting_question)
    await callback.answer()


async def answer_static_faq(callback: CallbackQuery, text: str):
    await callback.message.answer(text, reply_markup=assistant_faq_kb())
    await callback.answer()


@dp.callback_query(F.data == "faq_how")
async def faq_how(callback: CallbackQuery):
    await answer_static_faq(
        callback,
        "Занятия проходят онлайн в Zoom с использованием интерактивной доски. "
        "Дина демонстрирует экран, а ученик сам взаимодействует с заданиями и "
        "управляет интерактивными материалами. Обычное занятие длится 60 минут.",
    )


@dp.callback_query(F.data == "faq_price")
async def faq_price(callback: CallbackQuery):
    await answer_static_faq(
        callback,
        "Группа до 4 человек — 1000 ₽ / 60 минут.\n"
        "В паре — 1300 ₽ / 60 минут.\n"
        "Индивидуально — 1500 ₽ / 60 минут.\n"
        "ОГЭ/ЕГЭ по английскому индивидуально — 2000 ₽ / 60 минут.",
    )


@dp.callback_query(F.data == "faq_trial")
async def faq_trial(callback: CallbackQuery):
    await answer_static_faq(
        callback,
        "Пробное занятие бесплатное и длится 30 минут. "
        "Записаться можно через кнопку «🎓 Бесплатный пробный урок» в главном меню.",
    )


@dp.callback_query(F.data == "faq_cancel")
async def faq_cancel(callback: CallbackQuery):
    await answer_static_faq(
        callback,
        "Если отмена или перенос происходят менее чем за 4 часа до занятия, "
        "урок подлежит полной оплате. Исключение — болезнь ученика при наличии "
        "справки от врача. Без справки оплата не возвращается.",
    )


@dp.message(Command("ask"))
async def ask_command(message: Message, state: FSMContext):
    await state.set_state(AskAI.waiting_question)
    await message.answer(
        "Напиши свой вопрос одним сообщением 🤖\n"
        "Можно задавать несколько вопросов подряд. Чтобы выйти — отправь /start."
    )


@dp.message(Command("trainers"))
async def trainers_command(message: Message):
    if await is_channel_subscriber(message.from_user.id):
        await message.answer(
            "🎮 Выбирай тренажёр или материал 👇",
            reply_markup=materials_list_kb(),
        )
    else:
        await message.answer(
            "🎮 Тренажёры доступны подписчикам канала Дины 💛\n\n"
            "Подпишись на канал и нажми «Проверить подписку».",
            reply_markup=materials_gate_kb(),
        )


@dp.message(AskAI.waiting_question)
async def ask_ai_receive(message: Message, state: FSMContext):
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
    normalized = normalize_slot_text(message.text)
    if normalized is None:
        await message.answer(
            "Не получилось добавить место ❌\n\n"
            "Занятие должно длиться ровно 60 минут и быть записано, например:\n"
            "<code>Среда 17:00–18:00</code>\n\n"
            "Попробуй ещё раз.",
            parse_mode="HTML",
        )
        return

    slots = load_slots()
    slots.append(normalized)
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
    await message.chat.do("typing")
    answer = await ask_kie(message.text, message.from_user.id)
    await message.answer(answer)


# ============ ЗАПУСК ============
async def setup_commands():
    # Это и есть выпадающее меню команд Telegram, о котором говорил тестировщик.
    common = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="ask", description="Спросить ассистента"),
        BotCommand(command="play", description="Мини-игры и квизы"),
        BotCommand(command="trainers", description="Тренажёры и материалы"),
    ]
    await bot.set_my_commands(common, scope=BotCommandScopeDefault())

    # У Дины дополнительно отображаются админ-команды.
    admin_commands = common + [
        BotCommand(command="okna", description="Управление свободными местами"),
        BotCommand(command="users", description="Статистика пользователей"),
    ]
    try:
        await bot.set_my_commands(
            admin_commands,
            scope=BotCommandScopeChat(chat_id=ADMIN_ID),
        )
    except Exception:
        logging.exception("Не удалось установить админ-команды")


async def main():
    await setup_commands()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
