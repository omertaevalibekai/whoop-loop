from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _home() -> Path:
    """Where the database, charts and .env live.

    A checkout (or the server copy) keeps them next to the code, as before.
    A pip-installed package must not write into site-packages, so it uses
    ~/.whoop-loop. WHOOP_LOOP_HOME overrides both.
    """
    override = os.environ.get("WHOOP_LOOP_HOME")
    if override:
        return Path(override).expanduser()
    source_root = Path(__file__).resolve().parent.parent
    if {"site-packages", "dist-packages"} & set(source_root.parts):
        return Path.home() / ".whoop-loop"
    return source_root


HOME = _home()


class Settings(BaseSettings):
    # Later files win: the working directory's .env overrides the home one.
    model_config = SettingsConfigDict(
        env_file=(HOME / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Whoop
    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    whoop_redirect_uri: str = "http://localhost:8000/callback"
    whoop_webhook_secret: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: int | None = None

    # Claude
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"

    # OpenAI (transcription only)
    openai_api_key: str = ""
    transcribe_model: str = "whisper-1"

    # Local
    db_url: str = "sqlite:///data/whoop.db"
    tz_name: str = "Asia/Almaty"
    digest_hour: int = 8
    digest_minute: int = 0
    # Whoop only scores a night once it has been confirmed in the app, which in
    # practice lands late morning. The digest waits inside this window for real
    # data instead of firing at a fixed hour on stale numbers.
    digest_deadline_hour: int = 14
    # Sunday recap and the daily "is anything still flowing" check.
    # The bedtime plan is only useful before the decision is made.
    evening_hour: int = 23
    evening_minute: int = 30
    weekly_report_hour: int = 20
    healthcheck_hour: int = 21
    # Evening question: was the recent illness alert real?
    feedback_hour: int = 20
    sync_interval_minutes: int = 30
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # iPhone widget: snapshot pushed to a secret gist (token needs "gist" scope)
    widget_github_token: str = ""
    widget_gist_id: str = ""

    @field_validator("telegram_chat_id", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        # An untouched key in .env arrives as "", which is not a valid int.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
