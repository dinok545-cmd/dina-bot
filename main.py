import asyncio
import threading

from bot import main as run_telegram_bot
from vk_bot import run_vk_bot


def start_vk_in_thread():
    thread = threading.Thread(target=run_vk_bot, daemon=True)
    thread.start()


if __name__ == "__main__":
    start_vk_in_thread()
    asyncio.run(run_telegram_bot())
