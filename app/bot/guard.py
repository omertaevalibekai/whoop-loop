"""Only the owner talks to the bot.

The bot answers health questions and sends the morning digest to "its" chat.
Without this guard anyone who found the bot could read the data with /health,
and a /start from a stranger re-bound the chat, so the owner's digests went
to them. Now the first chat to /start becomes the owner (or TELEGRAM_CHAT_ID
pins it), and every other chat gets no reply at all — not even a hint that
the bot exists.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import settings
from app.db import get_setting, session_scope

log = logging.getLogger(__name__)


def owner_chat_id() -> int | None:
    if settings.telegram_chat_id:
        return settings.telegram_chat_id
    with session_scope() as session:
        value = get_setting(session, "chat_id")
    return int(value) if value else None


def _chat_id(event: TelegramObject) -> int | None:
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery) and event.message is not None:
        return event.message.chat.id
    return None


def is_allowed(chat_id: int | None, text: str | None) -> bool:
    owner = owner_chat_id()
    if owner is None:
        # Nobody owns the bot yet: only /start may claim it.
        return bool(text) and text.split()[0].split("@")[0] == "/start"
    return chat_id == owner


class OwnerOnly(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = _chat_id(event)
        text = event.text if isinstance(event, Message) else None
        if is_allowed(chat_id, text):
            return await handler(event, data)
        log.warning("Чужой чат %s проигнорирован", chat_id)
        return None
