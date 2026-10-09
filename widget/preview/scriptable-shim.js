// Эмулятор API Scriptable для превью виджетов в браузере.
// Не пиксель-в-пиксель: шрифты и символы — ближайшие веб-аналоги. Задача —
// видеть композицию, цвета и иерархию, не заливая каждую правку на телефон.

const SCALE = 3

function css(c) {
  if (!c) return "transparent"
  const h = c.hex.replace("#", "")
  const full = h.length === 3 ? h.split("").map(x => x + x).join("") : h
  const n = parseInt(full.slice(0, 6), 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${c.alpha})`
}

class Color {
  constructor(hex, alpha = 1) { this.hex = hex; this.alpha = alpha }
  static white() { return new Color("#FFFFFF") }
  static black() { return new Color("#000000") }
  static clear() { return new Color("#000000", 0) }
  static gray() { return new Color("#8E8E93") }
  static dynamic(light, dark) { return dark }
}

class Size { constructor(w, h) { this.width = w; this.height = h } }
class Point { constructor(x, y) { this.x = x; this.y = y } }
class Rect {
  constructor(x, y, w, h) { Object.assign(this, { x, y, width: w, height: h }) }
  get minX() { return this.x } get minY() { return this.y }
}

const ROUNDED = '"SF Pro Rounded", ui-rounded, "Nunito", system-ui, sans-serif'
const PLAIN = '"SF Pro Text", -apple-system, "Inter", system-ui, sans-serif'
function mk(size, weight, rounded) { return { size, weight, family: rounded ? ROUNDED : PLAIN } }
const Font = {}
for (const [name, weight] of [["ultraLight", 200], ["thin", 250], ["light", 300], ["regular", 400],
  ["medium", 500], ["semibold", 600], ["bold", 700], ["heavy", 800], ["black", 900]]) {
  Font[`${name}SystemFont`] = s => mk(s, weight, false)
  Font[`${name}RoundedSystemFont`] = s => mk(s, weight, true)
  Font[`${name}MonospacedSystemFont`] = s => mk(s, weight, false)
}
Font.systemFont = s => mk(s, 400, false)
Font.boldSystemFont = s => mk(s, 700, false)
function fontCss(f) { return `${f.weight} ${f.size}px ${f.family}` }

// SF Symbols → Material Symbols (ближайшие по смыслу).
const SYMBOLS = {
  "moon.fill": "bedtime", "heart.fill": "favorite", "flame.fill": "local_fire_department",
  "scalemass.fill": "monitor_weight", "star.fill": "star", "hourglass": "hourglass_bottom",
  "fork.knife": "restaurant", "figure.run": "directions_run", "bolt.heart.fill": "ecg_heart",
  "exclamationmark.triangle.fill": "warning", "waveform.path.ecg": "monitor_heart",
  "wifi.slash": "wifi_off", "bed.double.fill": "bed", "drop.fill": "water_drop",
  "arrow.down": "south", "arrow.up": "north", "arrow.right": "east", "sparkles": "auto_awesome",
  "circle.fill": "circle", "chart.bar.fill": "bar_chart", "thermometer.medium": "thermostat",
  "lungs.fill": "pulmonology", "bolt.fill": "bolt", "clock.fill": "schedule",
}
class SFSymbol {
  constructor(name) { this.name = name }
  static named(name) { return new SFSymbol(name) }
  applyFont() {} applyBoldWeight() {} applySemiboldWeight() {} applyHeavyWeight() {}
  get image() { return { symbol: SYMBOLS[this.name] || "help" } }
}

class LinearGradient {
  constructor() { this.colors = []; this.locations = []; this.startPoint = new Point(0, 0); this.endPoint = new Point(0, 1) }
}

class Path {
  constructor() { this.ops = [] }
  move(p) { this.ops.push(["M", p]) }
  addLine(p) { this.ops.push(["L", p]) }
  addCurve(p, c1, c2) { this.ops.push(["C", p, c1, c2]) }
  addQuadCurve(p, c) { this.ops.push(["Q", p, c]) }
  addRect(r) { this.ops.push(["R", r]) }
  addRoundedRect(r, cw, ch) { this.ops.push(["RR", r, cw]) }
  addEllipse(r) { this.ops.push(["E", r]) }
  closeSubpath() { this.ops.push(["Z"]) }
}

class DrawContext {
  constructor() {
    this.size = new Size(100, 100); this.opaque = true; this.respectScreenScale = false
    this._c = null; this._align = "left"; this._font = Font.systemFont(12)
    this._text = Color.black(); this._fill = Color.black(); this._stroke = Color.black(); this._lw = 1
    this._path = null
  }
  get ctx() {
    if (!this._c) {
      const cv = document.createElement("canvas")
      cv.width = this.size.width * SCALE; cv.height = this.size.height * SCALE
      this._c = cv.getContext("2d"); this._c.scale(SCALE, SCALE)
    }
    return this._c
  }
  setFillColor(c) { this._fill = c } setStrokeColor(c) { this._stroke = c } setLineWidth(w) { this._lw = w }
  setFont(f) { this._font = f } setTextColor(c) { this._text = c }
  setTextAlignedLeft() { this._align = "left" } setTextAlignedCenter() { this._align = "center" }
  setTextAlignedRight() { this._align = "right" }
  fillRect(r) { const x = this.ctx; x.fillStyle = css(this._fill); x.fillRect(r.x, r.y, r.width, r.height) }
  fillEllipse(r) {
    const x = this.ctx; x.fillStyle = css(this._fill); x.beginPath()
    x.ellipse(r.x + r.width / 2, r.y + r.height / 2, r.width / 2, r.height / 2, 0, 0, Math.PI * 2); x.fill()
  }
  strokeEllipse(r) {
    const x = this.ctx; x.strokeStyle = css(this._stroke); x.lineWidth = this._lw; x.beginPath()
    x.ellipse(r.x + r.width / 2, r.y + r.height / 2, r.width / 2, r.height / 2, 0, 0, Math.PI * 2); x.stroke()
  }
  addPath(p) { this._path = p }
  _trace() {
    const x = this.ctx; x.beginPath()
    for (const [op, a, b, c] of this._path.ops) {
      if (op === "M") x.moveTo(a.x, a.y)
      else if (op === "L") x.lineTo(a.x, a.y)
      else if (op === "C") x.bezierCurveTo(b.x, b.y, c.x, c.y, a.x, a.y)
      else if (op === "Q") x.quadraticCurveTo(b.x, b.y, a.x, a.y)
      else if (op === "R") x.rect(a.x, a.y, a.width, a.height)
      else if (op === "RR") x.roundRect(a.x, a.y, a.width, a.height, b)
      else if (op === "E") x.ellipse(a.x + a.width / 2, a.y + a.height / 2, a.width / 2, a.height / 2, 0, 0, Math.PI * 2)
      else if (op === "Z") x.closePath()
    }
  }
  fillPath() { this._trace(); this.ctx.fillStyle = css(this._fill); this.ctx.fill() }
  strokePath() {
    this._trace(); const x = this.ctx
    x.strokeStyle = css(this._stroke); x.lineWidth = this._lw; x.lineCap = "round"; x.stroke()
  }
  drawTextInRect(text, r) {
    const x = this.ctx; x.font = fontCss(this._font); x.fillStyle = css(this._text)
    x.textBaseline = "top"; x.textAlign = this._align
    const tx = this._align === "center" ? r.x + r.width / 2 : this._align === "right" ? r.x + r.width : r.x
    x.fillText(text, tx, r.y + (this._font.size * 0.12))
  }
  drawText(text, p) { this.drawTextInRect(text, new Rect(p.x, p.y, 1000, 1000)) }
  drawImageInRect(img, r) {
    if (img.canvas) this.ctx.drawImage(img.canvas, r.x, r.y, r.width, r.height)
  }
  getImage() { this.ctx; return { canvas: this._c.canvas, dataURL: this._c.canvas.toDataURL() } }
}

// ---------- стеки и виджет: flexbox с поведением WidgetKit ----------

class WidgetText {
  constructor(t) { this.text = t; this.font = Font.systemFont(16); this.textColor = null; this.lineLimit = 0
    this.minimumScaleFactor = 1; this.textOpacity = 1; this.align = "left" }
  leftAlignText() { this.align = "left" } centerAlignText() { this.align = "center" } rightAlignText() { this.align = "right" }
}
class WidgetImage {
  constructor(img) { this.image = img; this.imageSize = null; this.tintColor = null; this.imageOpacity = 1
    this.cornerRadius = 0; this.resizable = true }
  centerAlignImage() {} leftAlignImage() {} rightAlignImage() {}
}
class WidgetSpacer { constructor(n) { this.length = n } }

class WidgetStack {
  constructor() { this.children = []; this.vertical = false; this.spacing = 0; this.padding = [0, 0, 0, 0]
    this.align = "flex-start"; this.backgroundColor = null; this.backgroundGradient = null; this.cornerRadius = 0
    this.size = null; this.borderColor = null; this.borderWidth = 0; this.url = null }
  addStack() { const s = new WidgetStack(); this.children.push(s); return s }
  addText(t) { const x = new WidgetText(t); this.children.push(x); return x }
  addImage(i) { const x = new WidgetImage(i); this.children.push(x); return x }
  addSpacer(n) { this.children.push(new WidgetSpacer(n)) }
  addDate(d) { return this.addText(d.toLocaleTimeString("ru", { hour: "2-digit", minute: "2-digit" })) }
  layoutVertically() { this.vertical = true } layoutHorizontally() { this.vertical = false }
  centerAlignContent() { this.align = "center" } topAlignContent() { this.align = "flex-start" }
  bottomAlignContent() { this.align = "flex-end" }
  setPadding(t, l, b, r) { this.padding = [t, l, b, r] }
  useDefaultPadding() { this.padding = [16, 16, 16, 16] }
}

class ListWidget extends WidgetStack {
  constructor() { super(); this.vertical = true; this.padding = [16, 16, 16, 16]; this.refreshAfterDate = null
    this.addAccessoryWidgetBackground = false }
  async presentSmall() {} async presentMedium() {} async presentLarge() {}
}

function gradCss(g) {
  const angle = Math.atan2(g.endPoint.x - g.startPoint.x, -(g.endPoint.y - g.startPoint.y)) * 180 / Math.PI
  const stops = g.colors.map((c, i) => `${css(c)} ${(g.locations[i] ?? i / (g.colors.length - 1)) * 100}%`)
  return `linear-gradient(${angle}deg, ${stops.join(",")})`
}

function renderNode(node, parentVertical, tint) {
  if (node instanceof WidgetSpacer) {
    const d = document.createElement("div")
    d.style.flex = node.length == null ? "1 1 0" : `0 0 ${node.length}px`
    return d
  }
  if (node instanceof WidgetText) {
    const d = document.createElement("div")
    d.textContent = node.text
    d.style.font = fontCss(node.font)
    d.style.color = tint || (node.textColor ? css(node.textColor) : "#fff")
    d.style.opacity = node.textOpacity
    d.style.textAlign = node.align
    d.style.lineHeight = "1.2"
    d.style.minWidth = "0"
    if (node.lineLimit === 1) Object.assign(d.style, { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" })
    return d
  }
  if (node instanceof WidgetImage) {
    const s = node.imageSize || { width: 20, height: 20 }
    if (node.image.symbol) {
      const d = document.createElement("span")
      d.className = "material-symbols-rounded"
      d.textContent = node.image.symbol
      Object.assign(d.style, { fontSize: `${s.height * 1.15}px`, width: `${s.width}px`, height: `${s.height}px`,
        lineHeight: `${s.height}px`, textAlign: "center", overflow: "visible", flex: "0 0 auto",
        color: tint || (node.tintColor ? css(node.tintColor) : "#fff"), display: "inline-block",
        fontVariationSettings: '"FILL" 1, "wght" 600' })
      return d
    }
    const img = document.createElement("img")
    img.src = node.image.dataURL
    Object.assign(img.style, { width: `${s.width}px`, height: `${s.height}px`, flex: "0 0 auto",
      borderRadius: `${node.cornerRadius}px`, opacity: node.imageOpacity })
    if (tint) img.style.filter = "brightness(0) invert(1)"
    return img
  }
  const d = document.createElement("div")
  const [t, l, b, r] = node.padding
  Object.assign(d.style, { display: "flex", flexDirection: node.vertical ? "column" : "row",
    gap: `${node.spacing}px`, padding: `${t}px ${r}px ${b}px ${l}px`, alignItems: node.align,
    borderRadius: `${node.cornerRadius}px`, minWidth: "0", boxSizing: "border-box", overflow: "hidden" })
  if (node.backgroundGradient) d.style.background = gradCss(node.backgroundGradient)
  else if (node.backgroundColor) d.style.background = css(node.backgroundColor)
  if (node.borderWidth) d.style.border = `${node.borderWidth}px solid ${css(node.borderColor)}`
  if (node.size && (node.size.width || node.size.height)) {
    if (node.size.width) d.style.width = `${node.size.width}px`
    if (node.size.height) d.style.height = `${node.size.height}px`
    d.style.flex = "0 0 auto"
  }
  // WidgetKit: стек с гибким спейсером занимает всё место вдоль своей оси.
  const flexible = node.children.some(c => c instanceof WidgetSpacer && c.length == null)
  if (flexible && !node.size) {
    if (node.vertical === parentVertical) d.style.flex = "1 1 0"
    else d.style.alignSelf = "stretch"
  }
  for (const ch of node.children) d.appendChild(renderNode(ch, node.vertical, tint))
  return d
}

// Размеры для iPhone 6.1" (15/16): маленький 158, средний 338×158.
const FAMILY_SIZE = {
  small: [158, 158], medium: [338, 158], large: [338, 354],
  accessoryCircular: [72, 72], accessoryRectangular: [160, 72], accessoryInline: [240, 20],
}

function renderWidget(widget, family) {
  const [w, h] = FAMILY_SIZE[family]
  const lock = family.startsWith("accessory")
  const root = renderNode(widget, true, lock ? "rgba(255,255,255,0.92)" : null)
  root.classList.add("widget", family)
  Object.assign(root.style, { width: `${w}px`, height: `${h}px`, flex: "0 0 auto" })
  if (!lock) {
    root.style.borderRadius = "22px"
    if (!widget.backgroundGradient && !widget.backgroundColor) root.style.background = "#1c1c1e"
  } else {
    root.style.padding = "0"
    root.style.background = widget.addAccessoryWidgetBackground ? "rgba(255,255,255,0.16)" : "transparent"
    if (family === "accessoryCircular") root.style.borderRadius = "50%"
    root.style.justifyContent = "center"
  }
  // ListWidget по умолчанию центрирует содержимое по вертикали.
  if (!lock && !widget.children.some(c => c instanceof WidgetSpacer && c.length == null)) {
    root.style.justifyContent = "center"
  }
  return root
}
