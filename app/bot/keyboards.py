from __future__ import annotations

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

# Nobody remembers slash commands for a bot they open once a day, so the
# everyday actions live on a keyboard that is always visible.
BTN_TODAY = "📊 Сегодня"
BTN_INSIGHTS = "💤 Разбор"
BTN_CHARTS = "📈 Графики"
BTN_WEEK = "📅 Неделя"
BTN_HEALTH = "🩺 Здоровье"
BTN_WEIGHT = "⚖️ Вес"
BTN_NIGHT = "🌙 Ночь"

ALL_BUTTONS = {
    BTN_TODAY, BTN_NIGHT, BTN_INSIGHTS,
    BTN_WEEK, BTN_CHARTS, BTN_HEALTH, BTN_WEIGHT,
}

MAIN = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_TODAY), KeyboardButton(text=BTN_NIGHT)],
        [KeyboardButton(text=BTN_INSIGHTS), KeyboardButton(text=BTN_WEEK)],
        [KeyboardButton(text=BTN_CHARTS), KeyboardButton(text=BTN_HEALTH)],
        [KeyboardButton(text=BTN_WEIGHT)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)
