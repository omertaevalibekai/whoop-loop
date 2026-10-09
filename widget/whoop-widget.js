// Whoop Loop — виджет для iPhone (Scriptable). Вставляется один раз.
// Сам код виджета лежит в gist и подтягивается отсюда: правки на сервере
// доезжают до телефона без повторного копирования.

const CORE_URL = "__CORE_URL__"   // готовую копию с адресом печатает `python cli.py widget setup`

const fm = FileManager.local()
const corePath = fm.joinPath(fm.documentsDirectory(), "whoop-core.js")

// Обновляем ядро не чаще раза в час; без сети работает прошлая версия.
const stale = !fm.fileExists(corePath) ||
  Date.now() - fm.modificationDate(corePath).getTime() > 60 * 60 * 1000
if (stale) {
  try {
    const req = new Request(CORE_URL + "?t=" + Date.now())
    req.timeoutInterval = 10
    const code = await req.loadString()
    if (code.includes("module.exports")) fm.writeString(corePath, code)
  } catch (e) {}
}

let widget
if (fm.fileExists(corePath)) {
  widget = await importModule(corePath)()
} else {
  widget = new ListWidget()
  widget.addText("Нет сети для первой загрузки")
}

if (config.runsInWidget) {
  Script.setWidget(widget)
} else {
  // В приложении: превью всех размеров по очереди не нужно — показываем средний.
  await widget.presentMedium()
}
Script.complete()
