"""Render the English workspace, including legacy language preferences, offline."""

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem  # noqa: F401 — register item conversions
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication
from qft.controller import Controller

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--screenshots", type=Path)
args = parser.parse_args()
if args.screenshots:
    args.screenshots.mkdir(parents=True, exist_ok=True)
app = QApplication([])
QQuickStyle.setStyle("Basic")
controller = Controller(demo=True)
engine = QQmlApplicationEngine()
warnings, failed, quit_events = [], [], []
engine.warnings.connect(lambda messages: warnings.extend(str(m) for m in messages))
engine.rootContext().setContextProperty("backend", controller)
engine.load(
    QUrl.fromLocalFile(str(Path(__file__).resolve().parent.parent / "qft/ui/Main.qml"))
)
if not engine.rootObjects():
    raise SystemExit("QML failed to load: " + str(warnings))
window = engine.rootObjects()[0]
controller.quitRequested.connect(lambda: quit_events.append(True))
sample = {
    "id": "adb:demo",
    "name": "DT50",
    "mode": "adb",
    "status": "device",
    "serial": "DEMO",
    "usb_path": "0-1",
}
firmware = "/Firmware/固件/SQ53ST_EN_XX_WE_DS_R01_U_250827_02_rel_modem_2.2_PRO"
programs = [
    {
        "label": "boot_a" if i == 0 else f"partition_{i}",
        "lun": i % 6,
        "start": str(1024 + i * 512),
        "sectors": 512,
        "sector_size": 4096,
    }
    for i in range(66)
]
patches = [
    {"lun": i % 6, "start": "1", "byte_offset": 16, "size": 4} for i in range(78)
]
console = "DEMO PREVIEW · no device commands\n\n[10:24:02] CHECKING · 66 writes / 78 patches\n[10:24:03] ENTERING_EDL · DT50\n[10:24:06] CONNECTING · same USB port\n[10:24:07] USB · configure / UFS\n[10:24:07] USB · getstorageinfo / LUN 0–5\n[10:24:08] PREFLIGHT · ranges checked\n[10:24:08] FLASHING · super_1.img\n[10:24:12] PASS · super_1.img\n[10:24:12] FLASHING · super_2.img\n[10:24:36] PASS · super_2.img\n[10:24:36] FLASHING · super_3.img\n"
steps = []


def obj(name):
    item = window.findChild(QObject, name)
    assert item is not None, name
    return item


def close_popups():
    for name in (
        "configurationDialog",
        "historyDialog",
        "flashConfirmation",
        "xmlEditorDialog",
        "folderConfirmation",
        "closeProtection",
    ):
        obj(name).close()


def capture(name):
    screenshot = window.grabWindow()
    assert not screenshot.isNull(), "Window could not render"
    if args.screenshots:
        assert screenshot.save(str(args.screenshots / f"{name}.png"))


def contained(item, width, height):
    assert item.property("x") >= 0 and item.property("y") >= 0
    assert item.property("x") + item.property("width") <= width + 1
    assert item.property("y") + item.property("height") <= height + 1


def contrast(color):
    values = [color.redF(), color.greenF(), color.blueF()]
    return sum(
        (v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) * w
        for v, w in zip(values, (0.2126, 0.7152, 0.0722))
    )


def check_footer(dialog):
    footer = dialog.property("footer")
    for name in ("dialogConfirm", "dialogCancel"):
        button = footer.findChild(QObject, name)
        assert button is not None and button.property("visible")
        label, background = (
            button.property("contentItem"),
            button.property("background"),
        )
        assert label.property("contentWidth") <= label.property("width") + 1, (
            "Clipped action label"
        )
        if button.property("enabled"):
            a, b = (
                contrast(label.property("color")),
                contrast(background.property("color")),
            )
            assert (max(a, b) + 0.05) / (min(a, b) + 0.05) >= 4.5, (
                "Unreadable button contrast"
            )


def prepare_case(lang, theme, view):
    close_popups()
    controller.action = "one_click" if view == "main" and theme == "dark" else ""
    busy = controller.action == "one_click"
    controller.update(
        language=lang,
        theme=theme,
        busy=busy,
        state="flashing" if busy else "ready",
        folder=firmware,
        loader=firmware + "/prog_firehose_qcm4290_ddr.elf",
        loaders=[firmware + "/prog_firehose_qcm4290_ddr.elf"],
        plan={"memory": "ufs", "programs": programs, "patches": patches},
        packageStatus="passed",
        devices=[sample],
        deviceIndex=0,
        progress=0.64 if busy else 0,
        progressKnown=busy,
        elapsed=82 if busy else 0,
        currentFile="super_3.img" if busy else "",
        transferred=3222378906,
        totalBytes=5034967040,
        logs=console,
        history=[
            {
                "action": "flash",
                "status": "success",
                "started": 1794491937,
                "message": "Flash completed and verification passed.",
            }
        ],
    )
    window.setWidth(430 if view == "confirm" else 520)
    window.setHeight(660 if view == "confirm" else 780)
    if view == "config":
        obj("configurationDialog").open()
    elif view == "history":
        obj("historyDialog").open()
    elif view == "confirm":
        controller.update(folder=firmware + "/" + "Very long firmware path/" * 30)
        obj("flashConfirmation").open()


def inspect_case(lang, theme, view):
    if view == "main":
        assert obj("phaseLabel").property("text") == (
            "Writing images" if theme == "dark" else "Ready to flash"
        ), "Legacy language changed the English interface"
        assert obj("configureButton").property("text") == "Configure…"
        console_panel = obj("consolePanel")
        assert console_panel.property("height") >= 150
        assert obj("configureButton").property("enabled") == (theme != "dark")
        assert obj("startFlashButton").property("visible") == (theme != "dark")
        assert obj("stopButton").property("visible") == (theme == "dark")
        assert window.property("currentStep") == (2 if theme == "dark" else 1)
    else:
        name = {
            "config": "configurationDialog",
            "history": "historyDialog",
            "confirm": "flashConfirmation",
        }[view]
        dialog = obj(name)
        assert dialog.property("visible")
        contained(dialog, window.width(), window.height())
        if view != "history":
            check_footer(dialog)
    capture(f"{view}-{lang}-{theme}")


for lang in ("en", "zh"):
    for theme in ("light", "dark"):
        for view in ("main", "config", "history", "confirm"):
            steps.append(
                (
                    f"open {lang}/{theme}/{view}",
                    lambda l=lang, t=theme, v=view: prepare_case(l, t, v),
                )
            )
            steps.append(
                (
                    f"render {lang}/{theme}/{view}",
                    lambda l=lang, t=theme, v=view: inspect_case(l, t, v),
                )
            )


def ready_preview():
    prepare_case("en", "dark", "main")
    controller.action = ""
    controller.update(
        busy=False,
        state="ready",
        currentFile="",
        progressKnown=False,
        progress=0,
        elapsed=0,
        logs="DEMO PREVIEW · no device commands\n\n[10:24:02] CHECKING · firmware files\n[10:24:03] PASS · loader / XML / images\n[10:24:03] PASS · 66 writes / 78 patches\n[10:24:04] DEVICE · DT50 / ADB\n[10:24:04] READY · confirm Flash to begin\n",
    )


def save_ready_preview():
    capture("ready-en-dark")


def minimum_window():
    close_popups()
    controller.action = ""
    controller.update(
        busy=False,
        state="idle",
        devices=[],
        deviceIndex=-1,
        logs="Log output with a very long line 固件\n" * 3000,
    )
    window.setWidth(430)
    window.setHeight(660)


def check_minimum():
    assert not obj("startFlashButton").property("enabled")
    panel = obj("consolePanel")
    assert panel.property("y") + panel.property("height") <= window.height() - 20, (
        "Console outside compact window"
    )
    assert panel.property("height") >= 150
    capture("minimum-window")
    files = [f"/Firmware/Long path/固件/rawprogram{i}.xml" for i in range(100)]
    patch_files = [f"/Firmware/Long path/固件/patch{i}.xml" for i in range(100)]
    controller.update(
        raws=files, patches=patch_files, selectedRaws=files, selectedPatches=patch_files
    )
    obj("configurationDialog").open()


def long_files():
    scroll = obj("configurationScroll")
    content = scroll.property("contentItem")
    assert content.property("contentHeight") > scroll.property("height"), (
        "Long file list cannot scroll"
    )
    capture("long-file-list")
    obj("configurationTabs").setProperty("currentIndex", 1)


def xml_editor():
    capture("execution-plan")
    controller.update(
        editorPath="/Demo/rawprogram0.xml",
        editorText="<data>\n<!-- Editable XML preview -->\n</data>",
    )
    controller.editorRequested.emit()


def folder_prompt():
    assert obj("xmlEditorDialog").property("visible")
    check_footer(obj("xmlEditorDialog"))
    capture("xml-editor")
    close_popups()
    controller.requestFolder(str(controller.store.root))
    controller.update(folderToConfirm="/Firmware/" + "Very long folder/" * 50 + "固件")


def check_folder():
    dialog = obj("folderConfirmation")
    assert dialog.property("visible") and not controller.proc
    assert controller.data["folderToConfirm"]
    contained(dialog, window.width(), window.height())
    check_footer(dialog)
    capture("folder-confirmation")
    dialog.close()


def stream_logs():
    controller.action = "one_click"
    controller.update(busy=True, state="flashing", packageIssues=[])
    controller.log("[STREAM] continuous console output 固件\n" * 1000)
    controller.handle("progress", {"done": 512, "total": 1024})


def check_stream():
    assert controller.data["logs"] in obj("consoleText").property("text")
    assert obj("stopButton").property("visible")
    assert obj("stopButton").property("enabled")
    assert window.property("currentStep") == 2


def close_while_flashing():
    assert not controller.data["folderToConfirm"]
    controller.action = "flash"
    controller.update(busy=True)
    window.close()


def check_close_guard():
    assert (
        obj("closeProtection").property("visible") and controller.data["closeBlocked"]
    )
    check_footer(obj("closeProtection"))
    obj("closeProtection").close()
    controller.action = "prepare"
    controller.update(busy=True)
    window.close()


def finish():
    assert quit_events and not controller.data["closeBlocked"]
    controller.update(busy=False)
    app.quit()


steps.extend(
    [
        (fn.__name__, fn)
        for fn in (
            ready_preview,
            save_ready_preview,
            minimum_window,
            check_minimum,
            long_files,
            xml_editor,
            folder_prompt,
            check_folder,
            stream_logs,
            stream_logs,
            stream_logs,
            stream_logs,
            stream_logs,
            check_stream,
            close_while_flashing,
            check_close_guard,
            finish,
        )
    ]
)
index = 0


def advance():
    global index
    if index >= len(steps):
        app.quit()
        return
    name, callback = steps[index]
    index += 1
    try:
        callback()
    except Exception as exc:
        failed.append(f"{name}: {exc!r}")
    QTimer.singleShot(100, advance)


QTimer.singleShot(150, advance)
app.exec()
controller.timer.stop()
del engine
if warnings or failed:
    raise SystemExit(str(warnings + failed))
print("16 UI configurations and large-list rendering: OK")
