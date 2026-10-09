from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings
from app.db import get_setting, init_db, session_scope
from app.whoop import sync as whoop_sync
from app.whoop.auth import exchange_code

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Whoop Loop", lifespan=lifespan)


def _page(title: str, body: str, ok: bool = True) -> HTMLResponse:
    color = "#4ec9a0" if ok else "#e0656f"
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ background:#12151c; color:#e6e8ee; font:16px/1.6 system-ui,sans-serif;
         display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
  .card {{ background:#171b24; border:1px solid #262b36; border-radius:14px;
           padding:32px 40px; max-width:520px; }}
  h1 {{ color:{color}; font-size:20px; margin:0 0 12px; }}
  p {{ color:#8b93a7; margin:0; }}
</style></head>
<body><div class="card"><h1>{title}</h1><p>{body}</p></div></body></html>"""
    return HTMLResponse(html, status_code=200 if ok else 400)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/callback")
def oauth_callback(
    background: BackgroundTasks,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if error:
        return _page("Whoop отказал в доступе", f"Причина: {error}", ok=False)
    if not code:
        return _page("Нет кода авторизации", "Whoop не передал параметр code.", ok=False)

    with session_scope() as session:
        expected = get_setting(session, "oauth_state")
    if expected and state and state != expected:
        return _page(
            "Не совпал state",
            "Запрос не похож на тот, что начинали вы. Повторите /auth.",
            ok=False,
        )

    try:
        exchange_code(code)
    except Exception as exc:
        return _page("Не удалось обменять код", str(exc), ok=False)

    # First link: pull a year of history so the analytics have something to chew on.
    background.add_task(whoop_sync.backfill, 365)
    return _page(
        "Whoop подключён",
        "Загружаю историю за год в фоне — это займёт пару минут. "
        "Можно закрыть вкладку и вернуться в Telegram.",
    )


def _valid_signature(body: bytes, signature: str | None, timestamp: str | None) -> bool:
    if not settings.whoop_webhook_secret:
        return True  # verification disabled
    if not signature or not timestamp:
        return False
    digest = hmac.new(
        settings.whoop_webhook_secret.encode(),
        timestamp.encode() + body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


@app.post("/webhooks/whoop")
async def whoop_webhook(
    request: Request,
    background: BackgroundTasks,
    x_whoop_signature: str | None = Header(default=None),
    x_whoop_signature_timestamp: str | None = Header(default=None),
) -> JSONResponse:
    body = await request.body()
    if not _valid_signature(body, x_whoop_signature, x_whoop_signature_timestamp):
        log.warning("Отклонён вебхук с неверной подписью")
        return JSONResponse({"status": "invalid signature"}, status_code=401)

    payload = await request.json()
    log.info("Вебхук Whoop: %s", payload.get("type"))

    # Whoop only tells us that something changed; the cheapest correct response
    # is to re-pull the last couple of days.
    background.add_task(whoop_sync.sync_recent, 2)
    return JSONResponse({"status": "ok"})
