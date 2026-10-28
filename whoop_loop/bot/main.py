from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from whoop_loop.bot.guard import OwnerOnly
from whoop_loop.bot.handlers import router
from whoop_loop.config import settings
from whoop_loop.db import init_db
from whoop_loop.scheduler import build_scheduler

log = logging.getLogger(__name__)


def create_bot() -> Bot:
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "Не задан TELEGRAM_BOT_TOKEN. Получи токен у @BotFather и положи в .env"
        )
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    init_db()

    bot = create_bot()
    dispatcher = Dispatcher()
    # Before any handler: strangers get silence, not health data.
    dispatcher.message.outer_middleware(OwnerOnly())
    dispatcher.callback_query.outer_middleware(OwnerOnly())
    dispatcher.include_router(router)

    scheduler = build_scheduler(bot)
    scheduler.start()
    log.info(
        "Планировщик запущен: синк каждые %d мин, сводка в %02d:%02d (%s)",
        settings.sync_interval_minutes,
        settings.digest_hour,
        settings.digest_minute,
        settings.tz_name,
    )

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
