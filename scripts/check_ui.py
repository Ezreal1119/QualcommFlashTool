"""Isolated Qt event-loop check for pages, translations, themes and long lists."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from qft.controller import Controller

app = QApplication([])
QQuickStyle.setStyle("Basic")
controller = Controller(demo=True)
engine = QQmlApplicationEngine()
warnings = []
engine.warnings.connect(lambda messages: warnings.extend(str(m) for m in messages))
engine.rootContext().setContextProperty("backend", controller)
engine.load(
    QUrl.fromLocalFile(str(Path(__file__).resolve().parent.parent / "qft/ui/Main.qml"))
)
if not engine.rootObjects():
    raise SystemExit("QML failed to load: " + str(warnings))
window = engine.rootObjects()[0]
cases = [
    (lang, theme, page)
    for lang in ("en", "zh")
    for theme in ("light", "dark")
    for page in range(4)
]
index = 0
failed = []


def advance():
    global index
    if window.grabWindow().isNull():
        failed.append("Window could not be rendered")
    if index < len(cases):
        lang, theme, page = cases[index]
        index += 1
        controller.update(language=lang, theme=theme, page=page)
        window.setWidth(1000)
        window.setHeight(740)
        QTimer.singleShot(70, advance)
    elif index == len(cases):
        index += 1
        raws = [
            f"/Firmware/Very long package directory name/固件/rawprogram{i}.xml"
            for i in range(100)
        ]
        patches = [
            f"/Firmware/Very long package directory name/固件/patch{i}.xml"
            for i in range(100)
        ]
        controller.update(
            page=0,
            raws=raws,
            patches=patches,
            selectedRaws=raws,
            selectedPatches=patches,
            logs=("Log output with a long diagnostic line\n" * 5000)[-120000:],
        )
        QTimer.singleShot(150, advance)
    else:
        app.quit()


QTimer.singleShot(150, advance)
app.exec()
controller.timer.stop()
del engine
if warnings or failed:
    raise SystemExit(str(warnings + failed))
print("16 UI configurations and large-list rendering: OK")
