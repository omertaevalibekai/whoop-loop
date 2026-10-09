from __future__ import annotations

import asyncio
import datetime as dt
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.analytics import guard, illness
from app.analytics.digest import build_digest, has_today_recovery
from app.analytics import evening
from app import widget
from app.analytics.energy import calibrate
from app.analytics.series import get_series
from app.analytics.weekly import build_weekly
from app.config import settings
from app.db import get_setting, session_scope, set_setting
from app.models import Alert
from app.util import fmt_num, now_local, today_local
from app.whoop import sync as whoop_sync
from app.whoop.auth import is_authorized

log = logging.getLogger(__name__)


def target_chat_id() -> int | None:
    if settings.telegram_chat_id:
        return settings.telegram_chat_id
    with session_scope() as session:
        value = get_setting(session, "chat_id")
    return int(value) if value else None


async def _send(bot: Bot, text: str) -> None:
    chat_id = target_chat_id()
    if chat_id is None:
        log.warning("Некуда отправлять: чат ещё не привязан, нажми /start в боте")
        return
    await bot.send_message(chat_id, text)


def _remember_alert(kind: str, level: str, message: str) -> bool:
    """Store an alert, returning False if the same one already went out today."""
    day = today_local()
    key = f"{kind}:{day.isoformat()}:{level}"
    with session_scope() as session:
        existing = session.query(Alert).filter(Alert.dedupe_key == key).first()
        if existing is not None:
            return False
        session.add(
            Alert(day=day, kind=kind, level=level, message=message, dedupe_key=key)
        )
    return True


def _note_sync(ok: bool) -> int:
    """Track consecutive sync failures so silence can be noticed."""
    with session_scope() as session:
        failures = 0 if ok else int(get_setting(session, "sync_failures", 0)) + 1
        set_setting(session, "sync_failures", failures)
    return failures


async def job_sync(bot: Bot) -> None:
    if not is_authorized():
        return
    try:
        await asyncio.to_thread(whoop_sync.sync_recent, 3)
        _note_sync(True)
    except Exception as exc:
        failures = _note_sync(False)
        log.warning("Синхронизация не удалась (%d подряд): %s", failures, exc)
        # Roughly three hours of silence at the default interval.
        if failures >= 6 and _remember_alert("sync_fail", "error", str(exc)):
            await _send(
                bot,
                "⚠️ <b>Не могу достучаться до Whoop</b>\n\n"
                f"Шесть попыток подряд не прошли. Последняя ошибка:\n"
                f"<code>{str(exc)[:300]}</code>\n\n"
                "Данные пока не обновляются. Если само не починится за час — "
                "скорее всего, отозван доступ приложения в аккаунте Whoop.",
            )
        return

    with session_scope() as session:
        report = illness.check(session)

    if report.level in ("watch", "alert") and _remember_alert(
        "illness", report.level, report.headline
    ):
        await _send(bot, report.render())


async def job_digest(bot: Bot) -> None:
    """Send the morning digest once today's night has actually been scored.

    Whoop only scores a night after it is confirmed in the app, which lands
    late morning. Firing at a fixed hour meant reporting yesterday's numbers,
    so this runs on a window: check, nudge, and give up at the deadline.
    """
    if not is_authorized():
        return

    day = today_local()
    stamp = day.isoformat()

    with session_scope() as session:
        if get_setting(session, "digest_sent_for") == stamp:
            return

    try:
        await asyncio.to_thread(whoop_sync.sync_recent, 2)
    except Exception as exc:
        log.warning("Синхронизация перед сводкой не удалась: %s", exc)

    past_deadline = now_local().hour >= settings.digest_deadline_hour

    with session_scope() as session:
        ready = has_today_recovery(session, day)
        text = build_digest(session, day) if (ready or past_deadline) else ""
        nudge_pending = (
            not ready
            and not past_deadline
            and get_setting(session, "digest_nudge_for") != stamp
        )
        if ready or past_deadline:
            set_setting(session, "digest_sent_for", stamp)
        elif nudge_pending:
            set_setting(session, "digest_nudge_for", stamp)

    if ready:
        await _send(bot, text)
        return

    if past_deadline:
        await _send(
            bot,
            text
            + "\n\n<i>Жду с "
            + f"{settings.digest_hour:02d}:00, но Whoop так и не отдал оценку. "
            + "Сводка выше — по тому, что есть.</i>",
        )
        return

    if nudge_pending:
        await _send(
            bot,
            "☀️ <b>Доброе утро</b>\n\n"
            "Whoop пока не оценил сегодняшнюю ночь — он ждёт, пока ты "
            "подтвердишь сон в приложении.\n\n"
            "Открой Whoop, подтверди — и я сразу пришлю полную сводку "
            "с восстановлением, сном и целью на день.",
        )


async def job_weekly(bot: Bot) -> None:
    if not is_authorized():
        return
    with session_scope() as session:
        row = calibrate(session)
        guard_report = guard.check(session)

    if row is None:
        return

    lines = [
        "<b>Недельная калибровка</b>",
        "",
        f"Реальный расход: <b>{fmt_num(row.tdee_observed)} ккал</b> "
        f"(Whoop показывал {fmt_num(row.mean_burn_kcal)})",
        f"Коэффициент: <b>{row.factor:.2f}</b>",
        f"Цель на неделю: <b>{fmt_num(row.target_kcal)} ккал/день</b>",
    ]
    if row.note:
        lines += ["", row.note]
    if guard_report.level != "green":
        lines += ["", guard_report.render()]

    await _send(bot, "\n".join(lines))


async def job_weekly_report(bot: Bot) -> None:
    """Sunday evening recap. Works on Whoop data alone — nothing to log."""
    if not is_authorized():
        return
    with session_scope() as session:
        text = build_weekly(session)
    await _send(bot, text)


async def job_healthcheck(bot: Bot) -> None:
    """Notice when the data dries up, instead of going quietly silent."""
    if not is_authorized():
        return

    day = today_local()
    with session_scope() as session:
        cycles = get_series(session, "burn_kcal", days=10, end_day=day)
        recoveries = get_series(session, "recovery", days=10, end_day=day)

    if not cycles:
        return

    stale_days = (day - max(cycles)).days
    if stale_days >= 2 and _remember_alert("stale_data", "warn", f"{stale_days} дн"):
        await _send(
            bot,
            f"⚠️ <b>Whoop молчит {stale_days} дн.</b>\n\n"
            f"Последние данные — за {max(cycles).strftime('%d.%m')}. "
            f"Обычно это значит, что браслет не синхронизировался с телефоном "
            f"или давно не заряжался.",
        )
        return

    # Data flows, but nights stay unscored: the app is waiting on confirmation.
    if recoveries:
        unscored = (day - max(recoveries)).days
        if unscored >= 3 and _remember_alert("unscored", "warn", f"{unscored} дн"):
            await _send(
                bot,
                f"⚠️ <b>Ночи не оценены {unscored} дн. подряд</b>\n\n"
                f"Данные с браслета приходят, но восстановление Whoop не считает — "
                f"он ждёт, пока ты подтвердишь сон в приложении.",
            )


async def job_evening(bot: Bot) -> None:
    """Bedtime target and tomorrow's forecast, sent while it still matters."""
    if not is_authorized():
        return
    with session_scope() as session:
        text = evening.render(session)
    await _send(bot, text)


async def job_widget() -> None:
    """Refresh the iPhone widget's gist; a no-op until the widget is set up."""
    if not is_authorized():
        return
    try:
        await asyncio.to_thread(widget.publish)
    except Exception as exc:
        log.warning("Не удалось обновить виджет: %s", exc)


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    scheduler.add_job(
        job_sync,
        IntervalTrigger(minutes=settings.sync_interval_minutes),
        args=[bot],
        id="sync",
        max_instances=1,
        coalesce=True,
    )
    # Re-check every 15 minutes across the morning window rather than firing
    # once; the job itself makes sure only one digest goes out per day.
    scheduler.add_job(
        job_digest,
        CronTrigger(
            hour=f"{settings.digest_hour}-{settings.digest_deadline_hour}",
            minute="*/15",
        ),
        args=[bot],
        id="digest",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_weekly,
        CronTrigger(
            day_of_week="mon",
            hour=settings.digest_hour,
            minute=(settings.digest_minute + 5) % 60,
        ),
        args=[bot],
        id="weekly",
        max_instances=1,
    )
    scheduler.add_job(
        job_evening,
        CronTrigger(hour=settings.evening_hour, minute=settings.evening_minute),
        args=[bot],
        id="evening",
        max_instances=1,
    )
    scheduler.add_job(
        job_weekly_report,
        CronTrigger(day_of_week="sun", hour=settings.weekly_report_hour, minute=0),
        args=[bot],
        id="weekly_report",
        max_instances=1,
    )
    # Offset from the sync so it publishes what the sync just pulled in; the
    # widget itself only refreshes every 15 minutes or so anyway.
    scheduler.add_job(
        job_widget,
        IntervalTrigger(minutes=15, start_date=now_local() + dt.timedelta(minutes=2)),
        id="widget",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        job_healthcheck,
        CronTrigger(hour=settings.healthcheck_hour, minute=30),
        args=[bot],
        id="healthcheck",
        max_instances=1,
    )
    return scheduler
