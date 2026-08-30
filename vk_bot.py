import logging
import os

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType

# ============ НАСТРОЙКИ ============
VK_TOKEN = os.getenv("VK_TOKEN", "PUT_YOUR_VK_COMMUNITY_TOKEN_HERE")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ID пользователей ВК, которым доступны настоящие ответы ИИ
ALLOWED_AI_VK_IDS = {2840329}

STUB_ANSWER = (
    "🤖 Это демо-режим ассистента (чтобы не тратить бесплатный лимит запросов).\n\n"
    "Пример того, как отвечает ИИ:\n"
    "«Привет! Я помогу разобраться с английским — грамматикой, словами, произношением "
    "или просто отвечу на вопрос по школьной программе. Пиши, что интересует!»\n\n"
    "Полный доступ к живым ответам ИИ есть у автора проекта."
)

logging.basicConfig(level=logging.INFO)


def ask_gemini(question: str) -> str:
    if not GEMINI_API_KEY:
        return "ИИ временно не настроен, попробуй позже."
    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            "Ты — дружелюбный помощник преподавателя английского языка и предметов "
            "начальной школы Дины Ахмедовой. Отвечай кратко, тепло и по делу.\n\n"
            f"Вопрос: {question}"
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception:
        logging.exception("Gemini error")
        return "Не получилось получить ответ от ИИ, попробуй чуть позже 🙏"


def run_vk_bot():
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)

    logging.info("VK bot started, listening for messages...")

    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            user_id = event.user_id
            text = event.text or ""

            if user_id in ALLOWED_AI_VK_IDS:
                answer = ask_gemini(text)
            else:
                answer = STUB_ANSWER

            try:
                vk.messages.send(
                    user_id=user_id,
                    message=answer,
                    random_id=0,
                )
            except Exception:
                logging.exception("Failed to send VK message")


if __name__ == "__main__":
    run_vk_bot()
