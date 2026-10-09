from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)


class TranscriptionUnavailable(RuntimeError):
    pass


def transcribe(audio: bytes, filename: str = "voice.ogg") -> str:
    """Turn a Telegram voice message into text.

    Telegram sends OGG/Opus, which the transcription endpoint accepts directly,
    so there is no ffmpeg step here.
    """
    if not settings.openai_api_key:
        raise TranscriptionUnavailable(
            "Не задан OPENAI_API_KEY — распознавание голоса отключено. "
            "Пришли то же самое текстом."
        )

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    result = client.audio.transcriptions.create(
        model=settings.transcribe_model,
        file=(filename, audio),
        language="ru",
    )
    text = getattr(result, "text", "") or ""
    log.info("Расшифровано %d символов", len(text))
    return text.strip()
