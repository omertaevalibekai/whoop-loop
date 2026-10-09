from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.analytics import charts, evening, feedback, guard, illness, insights
from app.analytics.digest import build_digest, build_short_status
from app.analytics.weekly import build_weekly
from app.bot import keyboards as kb
from app.analytics.energy import calibrate, compute_state
from app.db import get_setting, session_scope, set_setting, setting_or_default
from app.diary.extract import extract
from app.diary.ingest import ingest, log_intake, log_weight
from app.diary.transcribe import TranscriptionUnavailable, transcribe
from app.util import fmt_num, today_local
from app.whoop import sync as whoop_sync
from app.whoop.auth import build_authorize_url, is_authorized

log = logging.getLogger(__name__)
router = Router()

WEIGHT_RE = re.compile(r"^\s*(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:кг|kg)?\s*$", re.IGNORECASE)

HELP = """<b>Whoop Loop</b>

<b>Каждый день</b>
Просто пришли число — запишу вес: <code>82.4</code>
Голосовое или текст — разберу еду, алкоголь, кофе, стресс:
<i>«на обед плов и салат, две чашки кофе, лёг в час ночи»</i>

<b>Команды</b>
/today — состояние сейчас
/digest — утренняя сводка
/health — панель здоровья и риск заболеть
/sick — я заболел (учит детектор) · /vaccine — сегодня прививка
/accuracy — насколько детектор болезни угадывает именно у тебя
/guard — стоп-кран дефицита
/night — во сколько ложиться и что будет утром
/insights — что видно в твоих данных
/report — итоги недели
/week — недельная калибровка

<b>Графики</b>
/charts — весь набор
/weight /energy /recovery /sleep

<b>Настройка</b>
/goal cut 0.5 — режим и темп (cut / maintain / gain)
/protein 150 — целевой белок
/eat 650 плов — записать приём пищи вручную
/auth — подключить Whoop
/sync — синхронизировать сейчас"""


async def _bind_chat(message: Message) -> None:
    # Only the first chat claims the bot; the owner guard keeps everyone else
    # out, and this keeps a re-bind from ever moving the digests elsewhere.
    with session_scope() as session:
        if get_setting(session, "chat_id") is None:
            set_setting(session, "chat_id", message.chat.id)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await _bind_chat(message)
    if not is_authorized():
        await message.answer(
            "Привет. Я на связи, но Whoop ещё не подключён.\n"
            "Нажми /auth, чтобы связать аккаунт.",
            reply_markup=kb.MAIN,
        )
        return
    await message.answer(HELP, reply_markup=kb.MAIN)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("auth"))
async def cmd_auth(message: Message) -> None:
    await _bind_chat(message)
    url, state = build_authorize_url()
    with session_scope() as session:
        set_setting(session, "oauth_state", state)
    await message.answer(
        "Открой ссылку и разреши доступ. После этого вернёшься на локальную "
        "страницу с подтверждением — значит всё получилось.\n\n"
        f'<a href="{url}">Подключить Whoop</a>\n\n'
        "Важно: локальный сервер должен быть запущен (<code>python run_api.py</code>)."
    )


@router.message(Command("sync"))
async def cmd_sync(message: Message) -> None:
    if not is_authorized():
        await message.answer("Сначала подключи Whoop: /auth")
        return
    note = await message.answer("Синхронизирую…")
    try:
        counts = await asyncio.to_thread(whoop_sync.sync_recent, 14)
    except Exception as exc:
        await note.edit_text(f"Не получилось: {exc}")
        return
    await note.edit_text(
        "Готово: циклов {cycles}, снов {sleeps}, восстановлений {recoveries}, "
        "тренировок {workouts}.".format(**counts)
    )


@router.message(Command("today", "status"))
async def cmd_today(message: Message) -> None:
    with session_scope() as session:
        await message.answer(build_short_status(session))


@router.message(Command("digest"))
async def cmd_digest(message: Message) -> None:
    with session_scope() as session:
        await message.answer(build_digest(session))


def feedback_keyboard(day: dt.date) -> InlineKeyboardMarkup:
    stamp = day.isoformat()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=feedback.LABELS[feedback.SICK], callback_data=f"ill:{stamp}:{feedback.SICK}")],
        [InlineKeyboardButton(text=feedback.LABELS[feedback.OTHER], callback_data=f"ill:{stamp}:{feedback.OTHER}")],
        [InlineKeyboardButton(text=feedback.LABELS[feedback.FINE], callback_data=f"ill:{stamp}:{feedback.FINE}")],
    ])


@router.callback_query(F.data.startswith("ill:"))
async def on_illness_feedback(query: CallbackQuery) -> None:
    _, stamp, kind = query.data.split(":", 2)
    if kind not in feedback.LABELS:
        await query.answer()
        return
    with session_scope() as session:
        feedback.record(session, dt.date.fromisoformat(stamp), kind, source="feedback")
    await query.answer("Записал, спасибо")
    await query.message.edit_text(
        f"{query.message.html_text}\n\nОтвет: <b>{feedback.LABELS[kind]}</b>. "
        "Так детектор узнаёт, где он прав. Статистика — /accuracy"
    )


@router.message(Command("sick"))
async def cmd_sick(message: Message) -> None:
    with session_scope() as session:
        feedback.record(session, today_local(), feedback.SICK, source="self")
    await message.answer(
        "Отметил: сегодня ты болеешь. Выздоравливай.\n\n"
        "Если тревоги до этого не было — это пропущенный случай, и он тоже "
        "попадёт в /accuracy. Болеешь несколько дней — отмечай каждый день."
    )


@router.message(Command("vaccine"))
async def cmd_vaccine(message: Message) -> None:
    with session_scope() as session:
        feedback.record(session, today_local(), feedback.VACCINE, source="self")
    await message.answer(
        "Отметил прививку. Следующие две ночи пульс может подняться — "
        "это нормальная реакция, тревогу о болезни я в эти дни смягчу."
    )


@router.message(Command("accuracy"))
async def cmd_accuracy(message: Message) -> None:
    days = 180
    with session_scope() as session:
        text = feedback.track_record(session, today_local(), days).render(days)
    await message.answer(text)


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    with session_scope() as session:
        text = illness.check(session).render()
        path = await asyncio.to_thread(charts.chart_illness, session)
    await message.answer(text)
    await message.answer_photo(FSInputFile(path))


@router.message(Command("guard"))
async def cmd_guard(message: Message) -> None:
    with session_scope() as session:
        await message.answer(guard.check(session).render())


@router.message(Command("insights"))
async def cmd_insights(message: Message) -> None:
    with session_scope() as session:
        await message.answer(insights.render(session))


@router.message(Command("night", "evening"))
async def cmd_night(message: Message) -> None:
    with session_scope() as session:
        await message.answer(evening.render(session))


@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    with session_scope() as session:
        await message.answer(build_weekly(session))


@router.message(Command("week"))
async def cmd_week(message: Message) -> None:
    with session_scope() as session:
        row = calibrate(session)
        if row is None:
            state = compute_state(session)
            await message.answer(
                "Пока рано калибровать. Нужно минимум 10 дней с записанной едой "
                f"и 6 взвешиваний за две недели — сейчас дней с едой: {state.intake_days}.\n"
                "Контур заработает, как только наберётся история."
            )
            return

        lines = [
            "<b>Недельная калибровка</b>",
            "",
            f"Дней в расчёте: {row.days_counted}",
            f"Ел в среднем: {fmt_num(row.mean_intake_kcal)} ккал",
            f"Whoop насчитал расход: {fmt_num(row.mean_burn_kcal)} ккал",
            f"Вес двигался: {row.weight_slope_kg_per_day * 7:+.2f} кг/неделю",
            "",
            f"<b>Реальный расход: {fmt_num(row.tdee_observed)} ккал</b>",
            f"Коэффициент к Whoop: <b>{row.factor:.2f}</b>",
            f"Новая цель: <b>{fmt_num(row.target_kcal)} ккал/день</b>",
        ]
        if row.note:
            lines += ["", row.note]
        await message.answer("\n".join(lines))


@router.message(Command("goal"))
async def cmd_goal(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2 or parts[1] not in ("cut", "maintain", "gain"):
        await message.answer(
            "Формат: <code>/goal cut 0.5</code>\n"
            "Режимы: cut (снижение), maintain (поддержка), gain (набор).\n"
            "Второе число — темп в кг/неделю."
        )
        return

    mode = parts[1]
    rate = 0.0
    if len(parts) >= 3:
        try:
            rate = abs(float(parts[2].replace(",", ".")))
        except ValueError:
            await message.answer("Темп не распознан. Пример: <code>/goal cut 0.5</code>")
            return
    if mode == "maintain":
        rate = 0.0

    with session_scope() as session:
        set_setting(session, "goal_mode", mode)
        set_setting(session, "rate_kg_per_week", rate)
        state = compute_state(session)

    label = {"cut": "снижение", "maintain": "поддержка", "gain": "набор"}[mode]
    text = f"Режим: <b>{label}</b>"
    if rate:
        text += f", темп {rate} кг/неделю"
    if state.target_kcal:
        text += f"\nЦель на сегодня: <b>{fmt_num(state.target_kcal)} ккал</b>"
    if rate > 1.0:
        text += (
            "\n\nТемп выше килограмма в неделю почти всегда съедает мышцы и сон. "
            "Стоп-кран будет ругаться — и будет прав."
        )
    await message.answer(text)


@router.message(Command("protein"))
async def cmd_protein(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: <code>/protein 150</code>")
        return
    try:
        grams = float(parts[1].replace(",", "."))
    except ValueError:
        await message.answer("Не понял число.")
        return
    with session_scope() as session:
        set_setting(session, "protein_target_g", grams)
    await message.answer(f"Целевой белок: <b>{grams:.0f} г</b> в день.")


@router.message(Command("eat"))
async def cmd_eat(message: Message) -> None:
    raw = (message.text or "").split(maxsplit=1)
    if len(raw) < 2:
        await message.answer(
            "Формат: <code>/eat 650 плов</code> или просто опиши словами — разберу сам."
        )
        return

    body = raw[1]
    match = re.match(r"^\s*(\d{2,4})\s*(.*)$", body)
    if match:
        kcal = float(match.group(1))
        label = match.group(2).strip() or "Приём пищи"
        with session_scope() as session:
            log_intake(session, kcal, None, label)
            state = compute_state(session)
        text = f"✅ Записал {fmt_num(kcal)} ккал — {label}"
        if state.target_kcal:
            text += f"\nОсталось {fmt_num(state.remaining_kcal)} из {fmt_num(state.target_kcal)}."
        await message.answer(text)
        return

    await _handle_free_text(message, body)


@router.message(Command("charts"))
async def cmd_charts(message: Message) -> None:
    note = await message.answer("Рисую…")
    with session_scope() as session:
        paths = []
        for name in ("weight", "energy", "recovery", "sleep"):
            paths.append(await asyncio.to_thread(charts.ALL_CHARTS[name], session))
    await note.delete()
    for path in paths:
        await message.answer_photo(FSInputFile(path))


async def _single_chart(message: Message, name: str) -> None:
    with session_scope() as session:
        path = await asyncio.to_thread(charts.ALL_CHARTS[name], session)
    await message.answer_photo(FSInputFile(path))


@router.message(Command("weight"))
async def cmd_weight_chart(message: Message) -> None:
    parts = (message.text or "").split()
    if len(parts) >= 2:
        match = WEIGHT_RE.match(parts[1])
        if match:
            await _record_weight(message, float(match.group(1).replace(",", ".")))
            return
    await _single_chart(message, "weight")


@router.message(Command("energy"))
async def cmd_energy_chart(message: Message) -> None:
    await _single_chart(message, "energy")


@router.message(Command("recovery"))
async def cmd_recovery_chart(message: Message) -> None:
    await _single_chart(message, "recovery")


@router.message(Command("sleep"))
async def cmd_sleep_chart(message: Message) -> None:
    await _single_chart(message, "sleep")


async def _record_weight(message: Message, kg: float) -> None:
    if not 30 <= kg <= 250:
        await message.answer("Это не похоже на вес тела. Пример: <code>82.4</code>")
        return
    with session_scope() as session:
        log_weight(session, kg)
        state = compute_state(session)

    lines = [f"⚖️ Записал вес: <b>{kg:.1f} кг</b>"]
    if state.weight_smoothed_kg is not None:
        lines.append(f"Сглаженный тренд: {state.weight_smoothed_kg:.1f} кг")
    if state.slope_kg_per_week is not None:
        arrow = "↓" if state.slope_kg_per_week < 0 else (
            "↑" if state.slope_kg_per_week > 0 else "→"
        )
        lines.append(
            f"Движение: {arrow} {abs(state.slope_kg_per_week):.2f} кг/неделю"
        )
        lines.append(
            "<i>Дневные скачки — это вода и гликоген. Смотри на тренд, не на число.</i>"
        )
    await message.answer("\n".join(lines))


# --- keyboard buttons -----------------------------------------------------
# These must be registered before the catch-all text handler, or a button
# press gets parsed as a diary note.


@router.message(F.text == kb.BTN_TODAY)
async def btn_today(message: Message) -> None:
    await cmd_today(message)


@router.message(F.text == kb.BTN_NIGHT)
async def btn_night(message: Message) -> None:
    await cmd_night(message)


@router.message(F.text == kb.BTN_INSIGHTS)
async def btn_insights(message: Message) -> None:
    await cmd_insights(message)


@router.message(F.text == kb.BTN_CHARTS)
async def btn_charts(message: Message) -> None:
    await cmd_charts(message)


@router.message(F.text == kb.BTN_WEEK)
async def btn_week(message: Message) -> None:
    await cmd_report(message)


@router.message(F.text == kb.BTN_HEALTH)
async def btn_health(message: Message) -> None:
    await cmd_health(message)


@router.message(F.text == kb.BTN_WEIGHT)
async def btn_weight(message: Message) -> None:
    with session_scope() as session:
        state = compute_state(session)
    lines = ["⚖️ Пришли число — запишу вес. Например: <code>82.4</code>"]
    if state.weight_latest_kg is not None:
        lines.append(
            f"Последний замер: {state.weight_latest_kg:.1f} кг "
            f"({state.weight_latest_day.strftime('%d.%m')})"
        )
    else:
        lines.append(
            "Замеров пока нет. Взвешиваться лучше утром, до еды — тогда "
            "тренд получается чистым."
        )
    await message.answer("\n".join(lines))


@router.message(F.voice | F.audio)
async def on_voice(message: Message, bot: Bot) -> None:
    voice = message.voice or message.audio
    note = await message.answer("Слушаю…")
    try:
        file = await bot.get_file(voice.file_id)
        buffer = await bot.download_file(file.file_path)
        audio = buffer.read()
        text = await asyncio.to_thread(transcribe, audio)
    except TranscriptionUnavailable as exc:
        await note.edit_text(str(exc))
        return
    except Exception as exc:
        log.exception("Ошибка расшифровки")
        await note.edit_text(f"Не смог расшифровать: {exc}")
        return

    if not text:
        await note.edit_text("Ничего не разобрал. Попробуй ещё раз или напиши текстом.")
        return

    await note.edit_text(f"<i>«{text}»</i>")
    await _handle_free_text(message, text, kind="voice")


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message) -> None:
    text = (message.text or "").strip()

    match = WEIGHT_RE.match(text)
    if match:
        await _record_weight(message, float(match.group(1).replace(",", ".")))
        return

    await _handle_free_text(message, text)


async def _handle_free_text(message: Message, text: str, kind: str = "text") -> None:
    thinking = await message.answer("Разбираю…")
    try:
        extraction = await asyncio.to_thread(extract, text)
        with session_scope() as session:
            reply = ingest(session, extraction, text, kind=kind)
    except Exception as exc:
        log.exception("Ошибка разбора заметки")
        await thinking.edit_text(f"Не смог разобрать: {exc}")
        return
    await thinking.edit_text(reply)
