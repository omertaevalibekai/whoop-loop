// Whoop Loop — ядро виджета (Scriptable). Не вставляй его в Scriptable
// напрямую: это делает загрузчик whoop-widget.js, он же обновляет ядро сам.
// Размеры: маленький и средний на главном экране; круглый, прямоугольный
// и строка над часами на экране блокировки.
//
// Страница задаётся параметром виджета («Изменить виджет» → Parameter):
//   пусто / recovery — восстановление, sleep — сон, body — нагрузка и питание.
// Три виджета с разными параметрами в одной стопке листаются пальцем.
//
// Превью без телефона: widget/preview/index.html.

const DATA_URL = "__DATA_URL__"   // подставляет `python cli.py widget setup`
const BOT_URL = "__BOT_URL__"     // ссылка на бота, тоже подставляется

const C = {
  bg: "#0A0C0E",
  text: "#FFFFFF",
  dim: "#8B949C",
  faint: "#5B646B",
  tile: new Color("#FFFFFF", 0.06),
  green: "#2FE06B",
  yellow: "#FFC933",
  red: "#FF4057",
  none: "#6B747B",
  sleep: "#9AA8FF",
  strain: "#35A2FF",
  kcal: "#FF9F43",
}
const col = (hex, a = 1) => new Color(hex, a)

const PAGES = {
  "": "recovery", recovery: "recovery", "восстановление": "recovery",
  sleep: "sleep", "сон": "sleep",
  body: "body", strain: "body", "тело": "body", "нагрузка": "body",
}

function currentPage() {
  const raw = typeof args !== "undefined" && args.widgetParameter
    ? String(args.widgetParameter).trim().toLowerCase() : ""
  return PAGES[raw] || "recovery"
}

const SLEEP_TARGET_H = 8     // полное кольцо сна
const STRAIN_MAX = 21        // шкала нагрузки Whoop
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"]

// ---------- данные ----------

async function loadData() {
  const fm = FileManager.local()
  const cache = fm.joinPath(fm.cacheDirectory(), "whoop-widget.json")
  try {
    const req = new Request(DATA_URL + "?t=" + Date.now()) // обходим кэш CDN
    req.timeoutInterval = 10
    const data = await req.loadJSON()
    fm.writeString(cache, JSON.stringify(data))
    return { data, offline: false }
  } catch (e) {
    if (fm.fileExists(cache)) {
      return { data: JSON.parse(fm.readString(cache)), offline: true }
    }
    return { data: null, offline: true }
  }
}

// ---------- форматирование ----------

const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d))

function zoneOf(score) {
  if (score == null) return "none"
  if (score >= 67) return "green"
  if (score >= 34) return "yellow"
  return "red"
}

// Смешать два hex-цвета: t=0 — первый, t=1 — второй.
function mix(a, b, t) {
  const p = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16))
  const [x, y] = [p(a), p(b)]
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("")
}

// ---------- рисование ----------

function canvas(w, h) {
  const ctx = new DrawContext()
  ctx.size = new Size(w, h)
  ctx.opaque = false
  ctx.respectScreenScale = true
  return ctx
}

// Кольцо: мягкое свечение, тонкая дорожка и дуга с градиентом от
// приглушённого к яркому — так видно направление, а не просто заливку.
function gauge(fraction, value, unit, accent, size) {
  const ctx = canvas(size, size)
  const f = Math.max(0, Math.min(1, fraction || 0))
  const line = size * 0.085
  const r = (size - line) / 2 - size * 0.04
  const cx = size / 2, cy = size / 2
  const steps = 160
  const dot = (a, w, color) => {
    ctx.setFillColor(color)
    ctx.fillEllipse(new Rect(cx + r * Math.cos(a) - w / 2, cy + r * Math.sin(a) - w / 2, w, w))
  }
  const angle = i => -Math.PI / 2 + (2 * Math.PI * i) / steps

  for (let i = 0; i < steps; i++) dot(angle(i), line, col(accent, 0.13))
  const filled = Math.round(steps * f)
  for (let i = 0; i < filled; i++) dot(angle(i), line * 2.1, col(accent, 0.05))
  for (let i = 0; i < filled; i++) {
    dot(angle(i), line, col(mix(mix(accent, C.bg, 0.45), accent, i / Math.max(1, filled - 1))))
  }
  if (filled > 0) {
    const a = angle(filled - 1)
    dot(a, line * 1.25, col(accent))
    dot(a, line * 0.45, col("#FFFFFF", 0.9))
  }

  ctx.setTextAlignedCenter()
  ctx.setTextColor(col(C.text))
  const big = size * (value.length > 3 ? 0.25 : 0.29)
  ctx.setFont(Font.heavyRoundedSystemFont(big))
  const unitH = unit ? size * 0.11 : 0
  const top = cy - big * 0.62 - unitH / 2
  ctx.drawTextInRect(value, new Rect(0, top, size, big * 1.25))
  if (unit) {
    ctx.setFont(Font.semiboldRoundedSystemFont(size * 0.1))
    ctx.setTextColor(col(C.dim))
    ctx.drawTextInRect(unit, new Rect(0, top + big * 1.08, size, unitH * 1.4))
  }
  return ctx.getImage()
}

// Неделя столбиками с подписями дней; сегодня — ярко, остальные приглушены.
// goal рисует пунктир нормы (для сна — 7 ч).
function weekChart(values, days, width, height, max, colorOf, goal) {
  const ctx = canvas(width, height)
  const labelH = 10
  const chartH = height - labelH - 2
  const n = values.length
  const gap = 6
  const bw = (width - gap * (n - 1)) / n
  if (goal != null) {
    const gy = chartH - (chartH * goal) / max
    ctx.setFillColor(col("#FFFFFF", 0.18))
    for (let x = 0; x < width; x += 5) ctx.fillRect(new Rect(x, gy, 2.5, 1))
  }
  values.forEach((v, i) => {
    const x = i * (bw + gap)
    const today = i === n - 1
    const h = v == null ? 3 : Math.max(4, Math.min(chartH, (chartH * v) / max))
    const path = new Path()
    path.addRoundedRect(new Rect(x, chartH - h, bw, h), Math.min(4, bw / 2), Math.min(4, bw / 2))
    ctx.addPath(path)
    ctx.setFillColor(v == null ? col("#FFFFFF", 0.1) : col(colorOf(v), today ? 1 : 0.55))
    ctx.fillPath()
    ctx.setFont(today ? Font.boldSystemFont(8.5) : Font.mediumSystemFont(8.5))
    ctx.setTextColor(col(today ? C.text : C.faint))
    ctx.setTextAlignedCenter()
    ctx.drawTextInRect(days[i], new Rect(x - 4, chartH + 3, bw + 8, labelH))
  })
  return ctx.getImage()
}

function progressBar(fraction, width, height, accent) {
  const ctx = canvas(width, height)
  const bg = new Path()
  bg.addRoundedRect(new Rect(0, 0, width, height), height / 2, height / 2)
  ctx.addPath(bg)
  ctx.setFillColor(col(accent, 0.15))
  ctx.fillPath()
  const f = Math.max(0, Math.min(1, fraction || 0))
  if (f > 0) {
    const fg = new Path()
    fg.addRoundedRect(new Rect(0, 0, Math.max(height, width * f), height), height / 2, height / 2)
    ctx.addPath(fg)
    ctx.setFillColor(col(accent))
    ctx.fillPath()
  }
  return ctx.getImage()
}

function weekDays(data) {
  const end = new Date(data.day + "T12:00:00")
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(end)
    d.setDate(end.getDate() - (6 - i))
    return WEEKDAYS[d.getDay()]
  })
}

// ---------- блоки интерфейса ----------

function text(stack, value, font, color, opts = {}) {
  const t = stack.addText(value)
  t.font = font
  t.textColor = col(color)
  t.lineLimit = 1
  if (opts.scale) t.minimumScaleFactor = opts.scale
  return t
}

function symbol(stack, name, size, color) {
  const img = stack.addImage(SFSymbol.named(name).image)
  img.imageSize = new Size(size, size)
  img.tintColor = col(color)
  return img
}

function pill(stack, label, color, icon) {
  const p = stack.addStack()
  p.backgroundColor = col(color, 0.16)
  p.cornerRadius = 8
  p.setPadding(2, 7, 2, 7)
  p.centerAlignContent()
  if (icon) {
    symbol(p, icon, 9, color)
    p.addSpacer(3)
  }
  text(p, label, Font.boldSystemFont(10), color)
}

function header(w, title, icon, accent, status) {
  const h = w.addStack()
  h.centerAlignContent()
  symbol(h, icon, 11, accent)
  h.addSpacer(5)
  text(h, title, Font.boldSystemFont(11), C.dim)
  h.addSpacer()
  if (status) pill(h, status.label, status.color, status.icon)
}

// Плитка: подпись сверху, крупное значение и единица снизу.
function tile(stack, caption, value, unit, accent, opts = {}) {
  const t = stack.addStack()
  t.layoutVertically()
  t.backgroundColor = C.tile
  t.cornerRadius = 12
  t.setPadding(7, 9, 7, 9)
  if (opts.width) t.size = new Size(opts.width, opts.height || 0)
  if (opts.url) t.url = opts.url
  text(t, caption, Font.boldSystemFont(8.5), C.dim)
  t.addSpacer(2)
  const v = t.addStack()
  v.bottomAlignContent()
  text(v, value, Font.boldRoundedSystemFont(17), accent || C.text, { scale: 0.7 })
  if (unit) {
    v.addSpacer(2)
    text(v, unit, Font.semiboldRoundedSystemFont(10), C.dim)
  }
  v.addSpacer()
  return t
}

function wideTile(stack, caption, right, width, height) {
  const t = stack.addStack()
  t.layoutVertically()
  t.backgroundColor = C.tile
  t.cornerRadius = 12
  t.setPadding(7, 9, 6, 9)
  t.size = new Size(width, height)
  const top = t.addStack()
  text(top, caption, Font.boldSystemFont(8.5), C.dim)
  top.addSpacer()
  if (right) text(top, right, Font.semiboldSystemFont(8.5), C.dim)
  t.addSpacer(3)
  return t
}

// ---------- модель страниц ----------
// Одна модель на страницу, а рисуют её и средний, и маленький виджет.

function statusFor(data, offline, page) {
  if (offline) return { label: "нет сети", color: C.dim, icon: "wifi.slash" }
  if (data.alarm) {
    return {
      label: data.illness === "alert" ? "заболеваешь?" : "вне нормы",
      color: data.illness === "alert" ? C.red : C.yellow,
      icon: "exclamationmark.triangle.fill",
    }
  }
  const st = data.streaks || {}
  if (page === "recovery" && !data.recovery.fresh) {
    return { label: "подтверди сон", color: C.yellow, icon: "moon.fill" }
  }
  if (page !== "body" && st.sleep >= 2) {
    return { label: `${st.sleep} ноч${st.sleep < 5 ? "и" : "ей"} 7ч+`, color: C.kcal, icon: "flame.fill" }
  }
  if (page === "recovery" && st.green >= 2) {
    return { label: `${st.green} дн в зелёном`, color: C.green, icon: "flame.fill" }
  }
  if (!data.updated) return null
  return { label: data.updated.slice(11, 16), color: C.faint, icon: "clock.fill" }
}

function pageModel(data, page) {
  const rec = data.recovery
  const sleep = data.sleep || {}
  const en = data.energy || {}
  const wt = data.weight || {}
  const st = data.streaks || {}
  const goal = st.sleep_goal_hours || 7

  if (page === "sleep") {
    return {
      title: "СОН", icon: "moon.fill", accent: C.sleep,
      gauge: [(sleep.hours || 0) / SLEEP_TARGET_H, fmt(sleep.hours, 1), "часов"],
      tiles: [
        ["КАЧЕСТВО", fmt(sleep.performance), "%"],
        ["ДОЛГ СНА", fmt(sleep.debt_hours, 1), "ч", sleep.debt_hours >= 1 ? C.kcal : null],
      ],
      wide: sleep.week ? {
        caption: "НЕДЕЛЯ", right: `норма ${fmt(goal)} ч`,
        chart: [sleep.week, 10, v => (v >= goal ? C.sleep : mix(C.sleep, C.red, 0.55)), goal],
      } : null,
      small: `качество ${fmt(sleep.performance)}%`,
    }
  }

  if (page === "body") {
    const eaten = en.eaten_kcal || 0
    const target = en.target_kcal
    const over = en.remaining_kcal != null && en.remaining_kcal < 0
    let trend = null
    if (wt.slope_kg_week != null) {
      const arrow = wt.slope_kg_week < -0.02 ? "↓" : wt.slope_kg_week > 0.02 ? "↑" : "→"
      trend = `${arrow}${fmt(Math.abs(wt.slope_kg_week), 1)}`
    }
    return {
      title: "НАГРУЗКА", icon: "figure.run", accent: C.strain,
      gauge: [(data.strain || 0) / STRAIN_MAX, fmt(data.strain, 1), `из ${STRAIN_MAX}`],
      tiles: [
        over
          ? ["ПЕРЕБОР", fmt(-en.remaining_kcal), "ккал", C.red]
          : ["ОСТАЛОСЬ", target != null ? fmt(en.remaining_kcal) : "—", "ккал", C.kcal],
        wt.kg != null
          ? ["ВЕС", fmt(wt.kg, 1), trend ? `кг ${trend}` : "кг"]
          : ["ВЕС", "+", "записать", C.dim, BOT_URL.startsWith("tg://") ? BOT_URL : null],
      ],
      wide: target != null ? {
        caption: "ПИТАНИЕ", right: `${fmt(eaten)} из ${fmt(target)} ккал`,
        progress: [eaten / target, over ? C.red : C.kcal],
      } : null,
      small: target == null ? "питание —"
        : over ? `перебор ${fmt(-en.remaining_kcal)} ккал` : `осталось ${fmt(en.remaining_kcal)} ккал`,
    }
  }

  const zone = rec.fresh ? rec.zone : "none"
  return {
    title: "ВОССТАНОВЛЕНИЕ", icon: "bolt.heart.fill", accent: C[zone],
    gauge: [rec.fresh ? (rec.score || 0) / 100 : 0, rec.fresh ? fmt(rec.score) : "—", rec.fresh ? "%" : "ждёт"],
    tiles: [
      ["HRV", fmt(rec.hrv), "мс"],
      ["ПУЛЬС ПОКОЯ", fmt(rec.rhr), "уд"],
    ],
    wide: data.alarm
      ? { caption: "ВНЕ НОРМЫ", note: (data.alarm.signals || []).join(" · ").toLowerCase() || data.alarm.headline }
      : { caption: "НЕДЕЛЯ", right: sleep.hours != null ? `сон ${fmt(sleep.hours, 1)} ч` : null,
          chart: [rec.week, 100, v => C[zoneOf(v)], null] },
    small: sleep.hours != null ? `сон ${fmt(sleep.hours, 1)} ч · ${fmt(sleep.performance)}%` : "",
  }
}

// ---------- сборка: главный экран ----------

function background(w, accent, data) {
  const g = new LinearGradient()
  g.locations = [0, 0.55, 1]
  let tint = mix(C.bg, accent, 0.16)
  if (data && data.alarm) tint = mix(C.bg, data.illness === "alert" ? C.red : C.yellow, 0.3)
  g.colors = [col(tint), col(C.bg), col(C.bg)]
  g.startPoint = new Point(0, 0)
  g.endPoint = new Point(1, 1)
  w.backgroundGradient = g
}

function buildHome(data, offline, family) {
  const w = new ListWidget()
  w.url = "whoop://"          // тап открывает приложение Whoop
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000)

  if (!data) {
    background(w, C.none, null)
    text(w, "Нет данных", Font.boldSystemFont(14), C.text)
    text(w, "Проверь интернет", Font.systemFont(12), C.dim)
    return w
  }

  const page = currentPage()
  const m = pageModel(data, page)
  const [fraction, value, unit] = m.gauge
  background(w, m.accent, data)
  const status = statusFor(data, offline, page)

  if (family === "small") {
    w.setPadding(12, 12, 12, 12)
    header(w, m.title === "ВОССТАНОВЛЕНИЕ" ? "RECOVERY" : m.title, m.icon, m.accent, null)
    w.addSpacer(4)
    const mid = w.addStack()
    mid.addSpacer()
    mid.addImage(gauge(fraction, value, unit, m.accent, 92)).imageSize = new Size(92, 92)
    mid.addSpacer()
    w.addSpacer(4)
    const foot = w.addStack()
    foot.addSpacer()
    const urgent = status && (data.alarm || status.label === "подтверди сон")
    text(foot, urgent ? status.label : m.small, Font.semiboldSystemFont(10.5),
      urgent ? status.color : C.dim, { scale: 0.8 })
    foot.addSpacer()
    return w
  }

  // medium: шапка, слева кольцо, справа две плитки и широкая плитка
  w.setPadding(13, 14, 13, 14)
  header(w, m.title, m.icon, m.accent, status)
  w.addSpacer(8)

  const body = w.addStack()
  body.centerAlignContent()
  body.addImage(gauge(fraction, value, unit, m.accent, 100)).imageSize = new Size(100, 100)
  body.addSpacer(12)

  const right = body.addStack()
  right.layoutVertically()
  right.spacing = 6
  const RW = 198
  const row = right.addStack()
  row.spacing = 6
  for (const [cap, val, u, accent, url] of m.tiles) {
    tile(row, cap, val, u, accent, { width: (RW - 6) / 2, height: 42, url })
  }

  const wide = m.wide
  if (wide) {
    const t = wideTile(right, wide.caption, wide.right, RW, 60)
    if (wide.chart) {
      const [vals, max, colorOf, goal] = wide.chart
      t.addImage(weekChart(vals, weekDays(data), RW - 18, 32, max, colorOf, goal))
        .imageSize = new Size(RW - 18, 32)
    } else if (wide.progress) {
      t.addSpacer()
      t.addImage(progressBar(wide.progress[0], RW - 18, 8, wide.progress[1]))
        .imageSize = new Size(RW - 18, 8)
      t.addSpacer()
    } else if (wide.note) {
      const n = t.addText(wide.note)
      n.font = Font.semiboldSystemFont(11)
      n.textColor = col(C.text)
      n.lineLimit = 2
      n.minimumScaleFactor = 0.8
    }
  }
  return w
}

// ---------- экран блокировки ----------
// iOS красит всё в один цвет и сама подкладывает фон, поэтому здесь только
// белый с прозрачностью: цвет всё равно не доживёт до экрана.

const LOCK = { on: Color.white(), off: new Color("#FFFFFF", 0.25), dim: new Color("#FFFFFF", 0.65) }

function lockGauge(fraction, value, size, alarm, caption) {
  const ctx = canvas(size, size)
  const line = size * 0.1
  const r = (size - line) / 2 - 1
  const steps = 110
  const filled = Math.round(steps * Math.max(0, Math.min(1, fraction || 0)))
  for (let i = 0; i < steps; i++) {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / steps
    ctx.setFillColor(i < filled ? LOCK.on : LOCK.off)
    ctx.fillEllipse(new Rect(
      size / 2 + r * Math.cos(a) - line / 2,
      size / 2 + r * Math.sin(a) - line / 2, line, line))
  }
  ctx.setTextAlignedCenter()
  ctx.setTextColor(LOCK.on)
  const big = size * (value.length > 3 ? 0.27 : 0.33)
  ctx.setFont(Font.heavyRoundedSystemFont(big))
  // Под числом место для «!» или подписи — число поднимаем.
  const lift = alarm || caption ? size * 0.07 : 0
  ctx.drawTextInRect(value, new Rect(0, size / 2 - big * 0.62 - lift, size, big * 1.3))
  if (alarm) {
    // Восклицательный знак под числом: цвет на экране блокировки
    // не виден, поэтому тревогу несёт форма, а не красный.
    ctx.setFont(Font.heavySystemFont(size * 0.2))
    ctx.drawTextInRect("!", new Rect(0, size * 0.58, size, size * 0.22))
  } else if (caption) {
    ctx.setFont(Font.boldSystemFont(size * 0.13))
    ctx.setTextColor(LOCK.dim)
    ctx.drawTextInRect(caption, new Rect(0, size * 0.6, size, size * 0.18))
  }
  return ctx.getImage()
}

function lockLine(stack, icon, value, bold) {
  const r = stack.addStack()
  r.centerAlignContent()
  const img = r.addImage(SFSymbol.named(icon).image)
  img.imageSize = new Size(bold ? 12 : 10, bold ? 12 : 10)
  img.tintColor = LOCK.on
  r.addSpacer(4)
  const t = r.addText(value)
  t.font = bold ? Font.heavyRoundedSystemFont(13.5) : Font.semiboldRoundedSystemFont(11.5)
  t.textColor = bold ? LOCK.on : LOCK.dim
  t.lineLimit = 1
  t.minimumScaleFactor = 0.7
}

function lockModel(data, page) {
  const rec = data.recovery
  const sleep = data.sleep || {}
  const en = data.energy || {}
  const wt = data.weight || {}
  const st = data.streaks || {}
  const alarm = !!data.alarm

  if (page === "sleep") {
    return {
      fraction: (sleep.hours || 0) / SLEEP_TARGET_H, value: fmt(sleep.hours, 1), caption: "сон",
      inline: `Сон ${fmt(sleep.hours, 1)} ч · ${fmt(sleep.performance)}%` +
        (st.sleep >= 2 ? ` · 🔥${st.sleep}` : ""),
      lines: [
        ["moon.fill", `${fmt(sleep.hours, 1)} ч сна`, true],
        ["star.fill", `качество ${fmt(sleep.performance)}%`],
        ["hourglass", `долг ${fmt(sleep.debt_hours, 1)} ч`],
      ],
    }
  }
  if (page === "body") {
    const kcal = en.target_kcal == null ? null
      : en.remaining_kcal < 0 ? `перебор ${fmt(-en.remaining_kcal)} ккал` : `ещё ${fmt(en.remaining_kcal)} ккал`
    return {
      fraction: (data.strain || 0) / STRAIN_MAX, value: fmt(data.strain, 1), caption: "нагр",
      inline: `Нагрузка ${fmt(data.strain, 1)}` +
        (kcal ? ` · ${kcal}` : ""),
      lines: [
        ["figure.run", `нагрузка ${fmt(data.strain, 1)}`, true],
        ["flame.fill", kcal || "ккал —"],
        ["scalemass.fill", wt.kg != null ? `${fmt(wt.kg, 1)} кг` : "вес не записан"],
      ],
    }
  }
  const score = rec.fresh ? rec.score : null
  const zoneName = { green: "зелёная зона", yellow: "жёлтая зона", red: "красная зона" }[rec.zone]
  const signals = ((data.alarm && data.alarm.signals) || []).join(", ").toLowerCase()
  return {
    fraction: score == null ? 0 : score / 100, value: score == null ? "—" : `${score}`,
    caption: "восст", alarm,
    inline: alarm ? `⚠︎ ${data.alarm.headline}`
      : [score != null ? `Recovery ${score}%` : "Whoop: подтверди сон",
         sleep.hours != null ? `сон ${fmt(sleep.hours, 1)} ч` : null,
         st.sleep >= 2 ? `🔥${st.sleep}` : null].filter(Boolean).join(" · "),
    lines: [
      alarm ? ["exclamationmark.triangle.fill", data.illness === "alert" ? "заболеваешь?" : "что-то не так", true]
        : ["bolt.heart.fill", score != null ? zoneName : "подтверди сон", true],
      ["moon.fill", sleep.hours != null ? `${fmt(sleep.hours, 1)} ч · ${fmt(sleep.performance)}%` : "сон —"],
      alarm && signals ? ["waveform.path.ecg", signals]
        : ["heart.fill", rec.hrv != null ? `HRV ${rec.hrv} · ${rec.rhr} уд` : "—"],
    ],
  }
}

function buildLock(data, family) {
  const w = new ListWidget()
  w.url = "whoop://"
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000)

  if (!data) {
    w.addText(family === "accessoryInline" ? "Whoop: нет данных" : "—")
    return w
  }
  const m = lockModel(data, currentPage())

  if (family === "accessoryInline") {
    w.addText(m.inline)
    return w
  }
  if (family === "accessoryCircular") {
    w.addAccessoryWidgetBackground = true
    const s = w.addStack()
    s.addSpacer()
    s.addImage(lockGauge(m.fraction, m.value, 62, m.alarm, m.caption)).imageSize = new Size(62, 62)
    s.addSpacer()
    return w
  }
  const main = w.addStack()
  main.centerAlignContent()
  main.addImage(lockGauge(m.fraction, m.value, 52, m.alarm)).imageSize = new Size(52, 52)
  main.addSpacer(9)
  const c = main.addStack()
  c.layoutVertically()
  c.spacing = 2
  for (const [icon, value, bold] of m.lines) lockLine(c, icon, value, bold)
  main.addSpacer()
  return w
}

// ---------- точка входа ----------

module.exports = async function main() {
  const { data, offline } = await loadData()
  const family = config.widgetFamily || "medium"
  return family.startsWith("accessory") ? buildLock(data, family) : buildHome(data, offline, family)
}
