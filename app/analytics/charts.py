from __future__ import annotations

import datetime as dt
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.analytics.baseline import build_baseline, ewma, trend_slope  # noqa: E402
from app.analytics.energy import compute_state  # noqa: E402
from app.analytics.illness import DIRECTION, PRIMARY, SUPPORTING  # noqa: E402
from app.analytics.series import LABELS, get_series  # noqa: E402
from app.util import today_local  # noqa: E402

CHART_DIR = Path(__file__).resolve().parent.parent.parent / "charts"
CHART_DIR.mkdir(exist_ok=True)

BG = "#12151c"
PANEL = "#171b24"
GRID = "#262b36"
FG = "#e6e8ee"
MUTED = "#8b93a7"

GREEN = "#4ec9a0"
YELLOW = "#e8c15c"
RED = "#e0656f"
BLUE = "#5fa8f5"
VIOLET = "#a98bf0"


def _style(fig, axes) -> None:
    fig.patch.set_facecolor(BG)
    for ax in axes:
        ax.set_facecolor(PANEL)
        ax.grid(True, color=GRID, linewidth=0.7, alpha=0.9)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_color(GRID)
        ax.tick_params(colors=MUTED, labelsize=9)
        ax.yaxis.label.set_color(MUTED)
        ax.xaxis.label.set_color(MUTED)
        ax.title.set_color(FG)


def _dates(ax, span_days: int) -> None:
    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))


def _save(fig, name: str) -> str:
    path = CHART_DIR / name
    fig.savefig(path, dpi=150, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def _empty(name: str, message: str) -> str:
    fig, ax = plt.subplots(figsize=(8, 3))
    _style(fig, [ax])
    ax.axis("off")
    ax.text(0.5, 0.5, message, ha="center", va="center", color=MUTED, fontsize=12)
    return _save(fig, name)


def chart_weight(session: Session, days: int = 90) -> str:
    series = get_series(session, "weight", days=days)
    if len(series) < 2:
        return _empty("weight.png", "Нужно минимум два взвешивания")

    smoothed = ewma(series)
    xs = sorted(series)
    slope_day = trend_slope(series, days=min(days, 28))

    fig, ax = plt.subplots(figsize=(9, 4.5))
    _style(fig, [ax])

    ax.plot(xs, [series[d] for d in xs], "o", color=MUTED, markersize=4,
            alpha=0.6, label="Замеры")
    ax.plot(xs, [smoothed[d] for d in xs], "-", color=GREEN, linewidth=2.4,
            label="Тренд")

    title = "Вес"
    if slope_day is not None:
        weekly = slope_day * 7
        arrow = "↓" if weekly < 0 else ("↑" if weekly > 0 else "→")
        title = f"Вес — тренд {arrow} {abs(weekly):.2f} кг/неделю"
    ax.set_title(title, fontsize=13, pad=12)
    ax.set_ylabel("кг")
    ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=FG, fontsize=9)
    _dates(ax, days)
    return _save(fig, "weight.png")


def chart_energy(session: Session, days: int = 30) -> str:
    burn = get_series(session, "burn_kcal", days=days)
    intake = get_series(session, "intake_kcal", days=days)
    if not burn and not intake:
        return _empty("energy.png", "Нет данных по расходу и еде")

    state = compute_state(session)
    all_days = sorted(set(burn) | set(intake))

    fig, ax = plt.subplots(figsize=(9, 4.5))
    _style(fig, [ax])

    if intake:
        eaten_days = sorted(intake)
        ax.bar(eaten_days, [intake[d] for d in eaten_days], width=0.7,
               color=BLUE, alpha=0.75, label="Съедено")
    if burn:
        burn_days = sorted(burn)
        ax.plot(burn_days, [burn[d] for d in burn_days], "-", color=YELLOW,
                linewidth=2.2, label="Расход (Whoop)")
    if state.target_kcal:
        ax.axhline(state.target_kcal, color=GREEN, linewidth=1.8,
                   linestyle="--", label=f"Цель {state.target_kcal:.0f}")

    ax.set_title("Энергетический баланс", fontsize=13, pad=12)
    ax.set_ylabel("ккал")
    ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=FG, fontsize=9)
    if all_days:
        ax.set_xlim(min(all_days) - dt.timedelta(days=1),
                    max(all_days) + dt.timedelta(days=1))
    _dates(ax, days)
    return _save(fig, "energy.png")


def chart_recovery(session: Session, days: int = 60) -> str:
    recovery = get_series(session, "recovery", days=days)
    hrv = get_series(session, "hrv", days=days)
    rhr = get_series(session, "rhr", days=days)
    if not recovery and not hrv:
        return _empty("recovery.png", "Нет данных по восстановлению")

    fig, axes = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
    _style(fig, list(axes))

    panels = [
        (axes[0], recovery, "Восстановление, %", GREEN),
        (axes[1], hrv, "HRV, мс", VIOLET),
        (axes[2], rhr, "Пульс покоя, уд/мин", RED),
    ]
    for ax, series, title, color in panels:
        if not series:
            ax.text(0.5, 0.5, "нет данных", transform=ax.transAxes,
                    ha="center", color=MUTED)
            ax.set_title(title, fontsize=11, pad=8)
            continue
        xs = sorted(series)
        ys = [series[d] for d in xs]
        ax.plot(xs, ys, "-", color=color, linewidth=2.0)
        ax.fill_between(xs, ys, min(ys), color=color, alpha=0.12)

        smoothed = ewma(series, alpha=0.25)
        ax.plot(xs, [smoothed[d] for d in xs], "--", color=FG,
                linewidth=1.2, alpha=0.65)
        ax.set_title(title, fontsize=11, pad=8)

    axes[0].set_title("Восстановление, % — сплошная линия, пунктир — сглаженный тренд",
                      fontsize=11, pad=8)
    _dates(axes[2], days)
    fig.tight_layout()
    return _save(fig, "recovery.png")


def chart_sleep(session: Session, days: int = 30) -> str:
    from app.models import Sleep
    from sqlalchemy import select

    end = today_local()
    start = end - dt.timedelta(days=days - 1)
    rows = list(
        session.scalars(
            select(Sleep)
            .where(Sleep.day >= start, Sleep.day <= end, Sleep.nap.is_(False))
            .order_by(Sleep.day)
        )
    )
    rows = [r for r in rows if r.light_ms is not None]
    if not rows:
        return _empty("sleep.png", "Нет данных по сну")

    xs = [r.day for r in rows]
    hours = lambda ms: (ms or 0) / 3_600_000.0  # noqa: E731
    deep = [hours(r.sws_ms) for r in rows]
    rem = [hours(r.rem_ms) for r in rows]
    light = [hours(r.light_ms) for r in rows]
    awake = [hours(r.awake_ms) for r in rows]

    fig, ax = plt.subplots(figsize=(9, 4.8))
    _style(fig, [ax])

    bottom_rem = deep
    bottom_light = [d + r for d, r in zip(deep, rem)]
    bottom_awake = [d + r + l for d, r, l in zip(deep, rem, light)]

    ax.bar(xs, deep, width=0.75, color=VIOLET, label="Глубокий")
    ax.bar(xs, rem, width=0.75, bottom=bottom_rem, color=BLUE, label="Быстрый")
    ax.bar(xs, light, width=0.75, bottom=bottom_light, color=GREEN, alpha=0.55,
           label="Лёгкий")
    ax.bar(xs, awake, width=0.75, bottom=bottom_awake, color=MUTED, alpha=0.5,
           label="Пробуждения")

    ax.set_title("Структура сна", fontsize=13, pad=12)
    ax.set_ylabel("часы")
    ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=FG, fontsize=9, ncol=4)
    _dates(ax, days)
    return _save(fig, "sleep.png")


def chart_illness(session: Session) -> str:
    """Deviation of each health signal from its personal baseline, in sigmas."""
    day = today_local()
    metrics = list(PRIMARY) + list(SUPPORTING)

    labels, values, colors = [], [], []
    for metric in metrics:
        series = get_series(session, metric, days=7, end_day=day)
        if not series:
            continue
        value = series.get(day, series[max(series)])
        baseline = build_baseline(session, metric, day)
        z = baseline.z(value)
        if z is None:
            continue
        # Flip sign so a positive bar always means "worse than normal".
        oriented = z * DIRECTION[metric]
        labels.append(LABELS.get(metric, metric))
        values.append(oriented)
        colors.append(RED if oriented >= 1.5 else (YELLOW if oriented >= 1.0 else GREEN))

    if not labels:
        return _empty("illness.png", "Недостаточно истории для базовой линии")

    fig, ax = plt.subplots(figsize=(8, 0.7 * len(labels) + 2.2))
    _style(fig, [ax])
    ax.barh(labels, values, color=colors, height=0.55)
    ax.axvline(0, color=MUTED, linewidth=1.0)
    ax.axvline(1.5, color=RED, linewidth=1.2, linestyle="--", alpha=0.8)
    ax.set_xlabel("Отклонение от нормы (вправо — хуже), сигм")
    ax.set_title("Панель здоровья", fontsize=13, pad=12)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    return _save(fig, "illness.png")


ALL_CHARTS = {
    "weight": chart_weight,
    "energy": chart_energy,
    "recovery": chart_recovery,
    "sleep": chart_sleep,
    "illness": chart_illness,
}
