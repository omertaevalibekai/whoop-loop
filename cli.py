from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import random
import sys

from app.db import init_db, session_scope, set_setting
from app.util import today_local


def cmd_init(_: argparse.Namespace) -> None:
    init_db()
    print("База создана.")


def cmd_auth(_: argparse.Namespace) -> None:
    from app.whoop.auth import build_authorize_url

    init_db()
    url, state = build_authorize_url()
    with session_scope() as session:
        set_setting(session, "oauth_state", state)
    print("1. Запусти локальный сервер в соседнем окне:  python run_api.py")
    print("2. Открой ссылку и разреши доступ:\n")
    print(url)


def cmd_exchange(args: argparse.Namespace) -> None:
    """Trade an authorization code for tokens by hand.

    Needed when the redirect URI cannot point at this machine, so the browser
    lands somewhere else and the code has to be copied out of the address bar.
    """
    from urllib.parse import parse_qs, urlparse

    from app.config import settings
    from app.whoop.auth import exchange_code

    init_db()
    raw = args.code.strip()

    # Accept either a bare code or the whole redirected URL.
    if raw.startswith("http://") or raw.startswith("https://"):
        query = parse_qs(urlparse(raw).query)
        found = query.get("code", [""])[0]
        if not found:
            print("В этой ссылке нет параметра code.")
            return
        raw = found

    print(f"Обмениваю код на токен (redirect_uri={settings.whoop_redirect_uri})...")
    try:
        exchange_code(raw)
    except Exception as exc:
        print(f"Не получилось: {exc}")
        print(
            "\nЧаще всего причина одна: WHOOP_REDIRECT_URI в .env не совпадает "
            "с тем, что зарегистрирован в приложении Whoop. Он должен совпадать "
            "символ в символ."
        )
        return

    print("Токен получен. Загружаю историю за год...")
    from app.whoop.sync import backfill

    print("Готово:", backfill(365))


def cmd_verify(_: argparse.Namespace) -> None:
    from app.whoop.sync import verify_endpoints

    init_db()
    print("Проверяю эндпоинты Whoop...\n")
    for name, status in verify_endpoints():
        print(f"  {name:<18} {status}")


def cmd_backfill(args: argparse.Namespace) -> None:
    from app.whoop.sync import backfill

    init_db()
    print(f"Загружаю историю за {args.days} дней...")
    counts = backfill(args.days)
    print("Готово:", counts)


def cmd_sync(args: argparse.Namespace) -> None:
    from app.whoop.sync import sync_recent

    init_db()
    counts = sync_recent(args.days)
    print("Готово:", counts)


def cmd_digest(_: argparse.Namespace) -> None:
    import re

    from app.analytics.digest import build_digest

    init_db()
    with session_scope() as session:
        text = build_digest(session)
    print(re.sub(r"</?[a-z]+>", "", text))


def cmd_calibrate(_: argparse.Namespace) -> None:
    from app.analytics.energy import calibrate

    init_db()
    with session_scope() as session:
        row = calibrate(session)
        if row is None:
            print("Недостаточно данных: нужно 10 дней с едой и 6 взвешиваний за 2 недели.")
            return
        print(f"Средний приход:      {row.mean_intake_kcal:.0f} ккал")
        print(f"Whoop насчитал:      {row.mean_burn_kcal:.0f} ккал")
        print(f"Вес:                 {row.weight_slope_kg_per_day * 7:+.2f} кг/неделю")
        print(f"Реальный расход:     {row.tdee_observed:.0f} ккал")
        print(f"Коэффициент:         {row.factor:.3f}")
        print(f"Цель:                {row.target_kcal:.0f} ккал/день")
        if row.note:
            print(row.note)


def cmd_charts(_: argparse.Namespace) -> None:
    from app.analytics import charts

    init_db()
    with session_scope() as session:
        for name, builder in charts.ALL_CHARTS.items():
            print(f"{name:<10} {builder(session)}")


def cmd_demo(args: argparse.Namespace) -> None:
    """Fill the database with plausible synthetic history.

    Lets the whole loop be exercised before the Whoop credentials exist.
    """
    from app.models import Cycle, IntakeLog, Recovery, Sleep, WeightLog

    init_db()
    random.seed(args.seed)
    days = args.days
    end = today_local()
    tz = dt.timezone.utc

    weight = 84.0
    with session_scope() as session:
        for offset in range(days, -1, -1):
            day = end - dt.timedelta(days=offset)
            index = days - offset
            # Illness builds over several nights in the studies ([ALAVI]: median 3 days
            # before symptoms), so the demo episode spans the last three nights.
            sick = args.with_illness and offset <= 2

            # Slow downward drift plus day-to-day water noise.
            weight -= 0.055 + random.gauss(0, 0.02)
            noisy_weight = weight + random.gauss(0, 0.45)

            base_hrv = 62 + 8 * math.sin(index / 9.0)
            hrv = base_hrv + random.gauss(0, 6) - (14 if sick else 0)
            rhr = 54 - 2 * math.sin(index / 9.0) + random.gauss(0, 2) + (9 if sick else 0)
            recovery = max(5, min(99, 55 + (hrv - 62) * 1.6 + random.gauss(0, 8)))
            if sick:
                recovery = max(8, recovery - 25)

            start = dt.datetime.combine(day, dt.time(4, 0), tzinfo=tz)
            session.merge(
                Cycle(
                    id=20000 + index,
                    day=day,
                    start=start,
                    end=start + dt.timedelta(hours=20),
                    score_state="SCORED",
                    strain=round(random.uniform(7.5, 16.5), 1),
                    kilojoule=round(random.gauss(11400, 900), 1),
                    average_heart_rate=int(random.gauss(72, 5)),
                    max_heart_rate=int(random.gauss(158, 10)),
                )
            )

            asleep_h = random.gauss(7.1, 0.8) - (0.6 if sick else 0)
            asleep_ms = int(max(4.0, asleep_h) * 3_600_000)
            session.merge(
                Sleep(
                    id=f"demo-{day.isoformat()}",
                    day=day,
                    start=dt.datetime.combine(
                        day - dt.timedelta(days=1), dt.time(23, 30), tzinfo=tz
                    ),
                    end=dt.datetime.combine(day, dt.time(7, 0), tzinfo=tz),
                    nap=False,
                    score_state="SCORED",
                    in_bed_ms=int(asleep_ms * 1.08),
                    awake_ms=int(asleep_ms * 0.08),
                    light_ms=int(asleep_ms * 0.54),
                    sws_ms=int(asleep_ms * 0.22),
                    rem_ms=int(asleep_ms * 0.24),
                    sleep_cycle_count=random.randint(3, 6),
                    disturbance_count=random.randint(2, 12) + (6 if sick else 0),
                    respiratory_rate=round(random.gauss(14.6, 0.5) + (1.6 if sick else 0), 1),
                    performance_pct=round(max(40, min(100, random.gauss(85, 9))), 0),
                    consistency_pct=round(random.gauss(72, 10), 0),
                    efficiency_pct=round(random.gauss(91, 3), 0),
                )
            )

            session.merge(
                Recovery(
                    cycle_id=20000 + index,
                    day=day,
                    sleep_id=f"demo-{day.isoformat()}",
                    score_state="SCORED",
                    recovery_score=round(recovery),
                    resting_heart_rate=round(rhr),
                    hrv_rmssd_milli=round(hrv, 1),
                    spo2_percentage=round(random.gauss(96.5, 0.6) - (1.2 if sick else 0), 1),
                    skin_temp_celsius=round(
                        random.gauss(33.4, 0.25) + (0.9 if sick else 0), 1
                    ),
                )
            )

            # Weigh in on most days, eat a little under target.
            if random.random() < 0.85:
                session.merge(
                    WeightLog(day=day, kg=round(noisy_weight, 1), source="demo")
                )
            if offset <= 30 and random.random() < 0.9:
                kcal = round(random.gauss(2250, 220))
                session.add(
                    IntakeLog(
                        day=day,
                        kcal=kcal,
                        protein_g=round(random.gauss(135, 25)),
                        label="Демо-день",
                        source="demo",
                    )
                )

        set_setting(session, "goal_mode", "cut")
        set_setting(session, "rate_kg_per_week", 0.5)

    print(f"Сгенерировано {days} дней демо-истории.")
    if args.with_illness:
        print("Последние три ночи размечены как начало болезни — проверь python cli.py digest")


def cmd_widget(args: argparse.Namespace) -> None:
    import json

    from app import widget
    from app.config import settings

    init_db()
    with session_scope() as session:
        snapshot = widget.build_snapshot(session)

    if args.action == "show":
        print(json.dumps(snapshot, ensure_ascii=False, indent=1))
        return

    if not settings.widget_github_token:
        print("Сначала впиши WIDGET_GITHUB_TOKEN в .env (токен с правом gist).")
        return

    if args.action == "setup":
        if widget.gist_id():
            print("Gist уже есть, новый не создаю.")
        else:
            widget.create_gist(json.dumps(snapshot, ensure_ascii=False))
            print("Секретный gist создан.")
        widget.publish(force=True)
        loader = widget.publish_scripts()
        print("\nСкрипт для Scriptable (откройте на iPhone, скопируйте целиком):\n")
        print(loader)
        print("\nСсылка секретная: по ней видны ваши данные. Никуда её не публикуйте.")
        return

    if args.action == "scripts":
        # After editing widget/*.js: phones pick the new core up within an hour.
        widget.publish_scripts()
        print("Скрипты виджета обновлены в gist.")
        return

    sent = widget.publish(force=True)
    print("Отправлено." if sent else "Не отправлено: сначала python cli.py widget setup")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whoop-loop")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="создать базу").set_defaults(func=cmd_init)
    sub.add_parser("auth", help="ссылка на авторизацию Whoop").set_defaults(func=cmd_auth)
    sub.add_parser("verify", help="проверить эндпоинты API").set_defaults(func=cmd_verify)
    sub.add_parser("digest", help="напечатать сводку").set_defaults(func=cmd_digest)
    sub.add_parser("calibrate", help="пересчитать коэффициент").set_defaults(func=cmd_calibrate)
    sub.add_parser("charts", help="построить все графики").set_defaults(func=cmd_charts)

    widget_cmd = sub.add_parser("widget", help="виджет для iPhone")
    widget_cmd.add_argument(
        "action", choices=["show", "setup", "push", "scripts"],
        help="show — показать JSON, setup — создать gist и скрипты, push — отправить данные, scripts — обновить код виджета",
    )
    widget_cmd.set_defaults(func=cmd_widget)

    backfill = sub.add_parser("backfill", help="загрузить историю")
    backfill.add_argument("--days", type=int, default=365)
    backfill.set_defaults(func=cmd_backfill)

    exchange = sub.add_parser(
        "exchange", help="обменять код авторизации вручную (можно вставить всю ссылку)"
    )
    exchange.add_argument("code")
    exchange.set_defaults(func=cmd_exchange)

    sync = sub.add_parser("sync", help="догрузить свежее")
    sync.add_argument("--days", type=int, default=7)
    sync.set_defaults(func=cmd_sync)

    demo = sub.add_parser("demo", help="заполнить базу синтетикой для проверки")
    demo.add_argument("--days", type=int, default=120)
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--with-illness", action="store_true")
    demo.set_defaults(func=cmd_demo)

    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
