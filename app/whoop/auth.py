from __future__ import annotations

import datetime as dt
import secrets
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.db import session_scope
from app.models import Token
from app.whoop.constants import AUTH_URL, SCOPES, TOKEN_URL


class NotAuthorized(RuntimeError):
    """Raised when there is no usable Whoop token on disk."""


def build_authorize_url(state: str | None = None) -> tuple[str, str]:
    """Return (url, state). Whoop requires a state of at least 8 characters."""
    state = state or secrets.token_urlsafe(16)
    params = {
        "client_id": settings.whoop_client_id,
        "redirect_uri": settings.whoop_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}", state


def _store(payload: dict) -> Token:
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        seconds=int(payload.get("expires_in", 3600))
    )
    with session_scope() as session:
        row = session.get(Token, 1)
        if row is None:
            row = Token(id=1, access_token="", expires_at=expires_at)
            session.add(row)
        row.access_token = payload["access_token"]
        # A refresh response may omit the refresh token; keep the previous one.
        if payload.get("refresh_token"):
            row.refresh_token = payload["refresh_token"]
        row.expires_at = expires_at
        row.scope = payload.get("scope", "")
        session.flush()
        session.expunge(row)
        return row


def _post_token(data: dict, auth: tuple[str, str] | None = None) -> httpx.Response:
    return httpx.post(
        TOKEN_URL,
        data=data,
        auth=auth,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )


def _raise_with_body(response: httpx.Response, what: str) -> None:
    """Surface the provider's own error text; a bare 400 says nothing useful."""
    raise RuntimeError(
        f"{what}: HTTP {response.status_code} — {response.text[:600] or '(пустое тело ответа)'}"
    )


def exchange_code(code: str) -> Token:
    base = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.whoop_redirect_uri,
    }

    # Providers differ on where the client credentials belong. Try the form
    # body first, then HTTP Basic, before giving up on a single code.
    attempts: list[tuple[str, dict, tuple[str, str] | None]] = [
        (
            "credentials in body",
            {
                **base,
                "client_id": settings.whoop_client_id,
                "client_secret": settings.whoop_client_secret,
            },
            None,
        ),
        (
            "HTTP Basic auth",
            base,
            (settings.whoop_client_id, settings.whoop_client_secret),
        ),
    ]

    last: httpx.Response | None = None
    for label, data, auth in attempts:
        response = _post_token(data, auth)
        if response.status_code < 400:
            return _store(response.json())
        last = response
        # An already-consumed or expired code will fail the same way twice;
        # only a client-auth complaint is worth the second shape.
        if response.status_code != 401:
            break

    if last is not None:
        _raise_with_body(last, "Обмен кода не удался")
    raise RuntimeError("Обмен кода не удался без ответа от сервера")


def refresh_token(current_refresh: str) -> Token:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": current_refresh,
        "client_id": settings.whoop_client_id,
        "client_secret": settings.whoop_client_secret,
        "scope": "offline",
    }
    response = _post_token(data)
    if response.status_code >= 400:
        _raise_with_body(response, "Обновление токена не удалось")
    return _store(response.json())


def get_access_token(force_refresh: bool = False) -> str:
    """Return a valid access token, refreshing it when it is close to expiry."""
    with session_scope() as session:
        row = session.get(Token, 1)
        if row is None:
            raise NotAuthorized(
                "Нет токена Whoop. Выполните авторизацию: python cli.py auth"
            )
        access = row.access_token
        refresh = row.refresh_token
        expires_at = row.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.timezone.utc)

    stale = expires_at - dt.timedelta(minutes=5) <= dt.datetime.now(dt.timezone.utc)
    if force_refresh or stale:
        if not refresh:
            raise NotAuthorized(
                "Токен истёк, а refresh_token отсутствует. "
                "Повторите авторизацию: python cli.py auth"
            )
        return refresh_token(refresh).access_token
    return access


def is_authorized() -> bool:
    with session_scope() as session:
        return session.get(Token, 1) is not None
