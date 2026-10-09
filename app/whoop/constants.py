from __future__ import annotations

API_BASE = "https://api.prod.whoop.com"
AUTH_URL = f"{API_BASE}/oauth/oauth2/auth"
TOKEN_URL = f"{API_BASE}/oauth/oauth2/token"
API_V2 = f"{API_BASE}/developer/v2"

# Collection endpoints, relative to API_V2.
EP_CYCLE = "/cycle"
EP_SLEEP = "/activity/sleep"
EP_RECOVERY = "/recovery"
EP_WORKOUT = "/activity/workout"
EP_PROFILE = "/user/profile/basic"
EP_BODY = "/user/measurement/body"

# `offline` is what grants a refresh token; without it the link dies in an hour.
SCOPES = [
    "offline",
    "read:recovery",
    "read:cycles",
    "read:sleep",
    "read:workout",
    "read:profile",
    "read:body_measurement",
]

# Whoop caps page size at 25 records.
PAGE_LIMIT = 25

# Partial map of Whoop sport ids. Unknown ids fall back to "Sport <id>";
# extend this as you see new ones in your own data.
SPORT_NAMES: dict[int, str] = {
    -1: "Активность",
    0: "Бег",
    1: "Велосипед",
    16: "Бейсбол",
    17: "Баскетбол",
    18: "Гребля",
    22: "Гольф",
    33: "Плавание",
    34: "Теннис",
    39: "Бокс",
    43: "Пилатес",
    44: "Йога",
    45: "Тяжёлая атлетика",
    48: "Функциональный тренинг",
    52: "Хайкинг",
    56: "Единоборства",
    59: "Пауэрлифтинг",
    60: "Скалолазание",
    63: "Ходьба",
    65: "Эллипс",
    66: "Степпер",
    70: "Медитация",
    71: "Другое",
    96: "HIIT",
    97: "Сайклинг",
}


def sport_name(sport_id: int | None) -> str:
    if sport_id is None:
        return "Тренировка"
    return SPORT_NAMES.get(sport_id, f"Спорт {sport_id}")
