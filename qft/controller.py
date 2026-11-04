import json
import sys
import time
from pathlib import Path
from PySide6.QtCore import (
    QObject,
    Property,
    Signal,
    Slot,
    QProcess,
    QProcessEnvironment,
    QTimer,
    QUrl,
)
from PySide6.QtWidgets import QFileDialog
from .models import PROTOCOL_VERSION
from .runtime import root
from .storage import Store, atomic_json

BUSY_STATES = {
    "checking",
    "connecting",
    "flashing",
    "patching",
    "verifying",
    "rebooting",
    "stopping",
}


class Controller(QObject):
    changed = Signal()

    def __init__(self, demo=False):
        super().__init__()
        self.store = Store()
        self.proc = None
        self.folder = None
        self.started = 0
        self.activity = 0
        self.phase_started = 0
        self.buffer = b""
        self.result = None
        self.action = ""
        self.task_dir = None
        self._data = {
            "page": 0,
            "busy": False,
            "state": "idle",
            "message": "Connect a device and choose an extracted firmware folder.",
            "devices": [],
            "deviceIndex": -1,
            "folder": "",
            "loaders": [],
            "loader": "",
            "raws": [],
            "patches": [],
            "selectedRaws": [],
            "selectedPatches": [],
            "memory": "ufs",
            "plan": {},
            "capacities": {},
            "warnings": [],
            "environment": [],
            "logs": "",
            "progress": 0.0,
            "transferred": 0,
            "totalBytes": 0,
            "progressKnown": False,
            "elapsed": 0,
            "currentFile": "",
            "result": {},
            "history": self.store.history(),
            "demo": demo,
            **self.store.settings,
        }
        # A remembered folder is displayed, but never trusted as a validated plan.
        self._data["folder"] = ""
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        if demo:
            self.demo()
        else:
            QTimer.singleShot(0, lambda: self.launch("environment", {}))

    @Property("QVariantMap", notify=changed)
    def data(self):
        return self._data

    def update(self, **values):
        self._data.update(values)
        self.changed.emit()

    def invalidate(self):
        self.update(
            plan={},
            capacities={},
            warnings=[],
            result={},
            state="idle",
            progressKnown=False,
            progress=0,
        )

    def editable(self):
        return not self._data["busy"]

    @Slot(int)
    def setPage(self, page):
        self.update(page=page)

    @Slot(str, "QVariant")
    def option(self, key, value):
        if not self.editable():
            return
        if key in ("language", "theme", "reboot"):
            self.store.settings[key] = value
            self.store.save_settings()
            self.update(**{key: value})
        elif key == "memory" and value in ("ufs", "emmc"):
            self.invalidate()
            self.update(memory=value)

    @Slot(int)
    def selectDevice(self, index):
        if self.editable():
            self.update(deviceIndex=index, capacities={}, result={})

    @Slot(str)
    def selectLoader(self, path):
        if self.editable():
            self.invalidate()
            self.update(loader=path)

    @Slot(str, str, bool)
    def toggleFile(self, kind, path, selected):
        if not self.editable():
            return
        key = "selectedRaws" if kind == "raw" else "selectedPatches"
        values = list(self._data[key])
        if selected and path not in values:
            values.append(path)
        if not selected and path in values:
            values.remove(path)
        # Retain discovery / manual insertion order, independent of click order.
        source = self._data["raws" if kind == "raw" else "patches"]
        values = [p for p in source if p in values]
        self.invalidate()
        self.update(**{key: values})

    @Slot()
    def chooseFolder(self):
        if not self.editable():
            return
        folder = QFileDialog.getExistingDirectory(
            None,
            "Choose extracted firmware",
            self.store.settings.get("last_folder", ""),
        )
        if folder:
            self.loadFolder(folder)

    @Slot(str)
    def dropFolder(self, url):
        if self.editable():
            self.loadFolder(QUrl(url).toLocalFile())

    @Slot(str)
    def loadFolder(self, folder):
        if not self.editable():
            return
        self.invalidate()
        self.update(
            folder=folder,
            loaders=[],
            loader="",
            raws=[],
            patches=[],
            selectedRaws=[],
            selectedPatches=[],
        )
        self.store.settings["last_folder"] = folder
        self.store.save_settings()
        self.launch("discover", {"folder": folder})

    @Slot(str)
    def chooseFiles(self, kind):
        if not self.editable():
            return
        if kind == "loader":
            path, _ = QFileDialog.getOpenFileName(
                None,
                "Choose Firehose loader",
                self._data["folder"],
                "Firehose (*.elf *.mbn)",
            )
            if path:
                values = list(self._data["loaders"])
                if path not in values:
                    values.append(path)
                self.invalidate()
                self.update(loaders=values, loader=path)
        else:
            paths, _ = QFileDialog.getOpenFileNames(
                None, "Choose XML files", self._data["folder"], "XML (*.xml)"
            )
            if paths:
                key = "raws" if kind == "raw" else "patches"
                sel = "selectedRaws" if kind == "raw" else "selectedPatches"
                values = list(dict.fromkeys([*self._data[key], *paths]))
                chosen = list(dict.fromkeys([*self._data[sel], *paths]))
                self.invalidate()
                self.update(**{key: values, sel: chosen})

    @Slot()
    def refresh(self):
        if self.editable():
            self.launch("scan", {})

    def device(self):
        i = self._data["deviceIndex"]
        devices = self._data["devices"]
        return devices[i] if 0 <= i < len(devices) else None

    @Slot()
    def enterEdl(self):
        dev = self.device()
        if self.editable() and dev:
            self.update(capacities={})
            self.launch("enter_edl", {"device": dev})

    @Slot()
    def validate(self):
        if not self.editable():
            return
        self.invalidate()
        self.launch(
            "validate",
            {
                k: self._data[v]
                for k, v in [
                    ("folder", "folder"),
                    ("loader", "loader"),
                    ("memory", "memory"),
                    ("raws", "selectedRaws"),
                    ("patches", "selectedPatches"),
                ]
            },
        )

    @Slot(str)
    def run(self, action):
        if not self.editable() or action not in ("inspect", "flash", "reset"):
            return
        dev = self.device()
        plan = self._data["plan"]
        if not dev or dev["mode"] != "edl" or not plan:
            self.update(message="Validate a package and select an EDL device first.")
            return
        if action == "flash" and not self._data["capacities"]:
            self.update(message="Inspect the device before flashing.")
            return
        self.launch(
            action,
            {
                "device": dev,
                "plan": plan,
                "reboot": self._data["reboot"],
                "capacities": self._data["capacities"] if action != "inspect" else {},
            },
        )

    def launch(self, action, payload):
        if self.proc or self._data["demo"]:
            return
        self.action = action
        self.result = None
        self.buffer = b""
        self.started = self.activity = self.phase_started = time.monotonic()
        request = {"version": PROTOCOL_VERSION, "action": action, **payload}
        self.task_dir = self.store.begin(action, request)
        self.update(
            busy=True,
            state="checking",
            message="Checking…",
            logs="",
            elapsed=0,
            result={},
            currentFile="",
            progress=0,
            transferred=0,
            totalBytes=0,
            progressKnown=False,
        )
        self.proc = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        if not getattr(sys, "frozen", False):
            environment.insert("PYTHONPATH", str(root()))
        environment.insert("PYTHONUNBUFFERED", "1")
        self.proc.setProcessEnvironment(environment)
        self.proc.setWorkingDirectory(str(self.task_dir))
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(
            ["--worker"]
            if getattr(sys, "frozen", False)
            else ["-m", "qft.app", "--worker"]
        )
        self.proc.readyReadStandardOutput.connect(self.read_wire)
        self.proc.readyReadStandardError.connect(self.read_logs)
        self.proc.finished.connect(self.finished)
        self.proc.errorOccurred.connect(self.process_error)
        self.proc.started.connect(
            lambda: self.proc.write((json.dumps(request) + "\n").encode())
            if self.proc
            else None
        )
        self.proc.start()

    def log(self, text):
        if self.task_dir:
            with (self.task_dir / "raw.log").open("a") as f:
                f.write(text)
        # A bounded display; the full log remains on disk.
        self.update(logs=(self._data["logs"] + text)[-120000:])

    def read_logs(self):
        if self.proc:
            self.log(
                bytes(self.proc.readAllStandardError()).decode(
                    "utf-8", errors="replace"
                )
            )

    def read_wire(self):
        if not self.proc:
            return
        self.buffer += bytes(self.proc.readAllStandardOutput())
        if len(self.buffer) > 8 * 1024 * 1024:
            self.result = {
                "status": "failed",
                "message": "Worker exceeded protocol message limit",
            }
            self.proc.kill()
            return
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
                if event["version"] != PROTOCOL_VERSION:
                    raise ValueError("Protocol version mismatch")
                with (self.task_dir / "events.jsonl").open("a") as f:
                    f.write(line.decode() + "\n")
                self.handle(event["kind"], event["data"])
            except Exception as exc:
                self.result = {
                    "status": "failed",
                    "message": f"Invalid worker response: {exc}",
                }
                self.proc.kill()
                return

    def handle(self, kind, data):
        if kind in ("activity", "progress"):
            self.activity = time.monotonic()
        if kind == "stage":
            self.phase_started = self.activity = time.monotonic()
            self.update(
                state=data["state"],
                currentFile=data.get("label") or data.get("file", ""),
                message=data["state"].capitalize() + "…",
            )
        elif kind == "progress":
            self.update(
                progress=data["done"] / max(1, data["total"]),
                transferred=data["done"],
                totalBytes=data["total"],
                progressKnown=True,
            )
        elif kind == "log":
            self.log(data.get("text", ""))
        elif kind == "devices":
            self.update(
                devices=data["devices"],
                deviceIndex=-1,
                capacities={},
                warnings=data["errors"],
            )
        elif kind == "environment":
            self.update(environment=data["checks"])
        elif kind == "package":
            # Duplicate indices must be resolved explicitly in the file selector.
            import re

            raw_indices = [
                re.search(r"(\d*)\.xml$", Path(p).name, re.I)[1] for p in data["raws"]
            ]
            chosen = data["raws"] if len(raw_indices) == len(set(raw_indices)) else []
            self.update(
                loaders=data["loaders"],
                loader=data["loaders"][0] if len(data["loaders"]) == 1 else "",
                raws=data["raws"],
                patches=data["patches"],
                selectedRaws=chosen,
                selectedPatches=data["patches"],
            )
        elif kind == "plan":
            self.update(
                plan=data,
                capacities={},
                warnings=[
                    "Device model / firmware compatibility has not been established."
                ],
            )
        elif kind == "preflight":
            self.update(
                capacities={str(k): v for k, v in data["capacities"].items()},
                warnings=data["warnings"] + [data["identity_match"]],
            )
            atomic_json(self.task_dir / "preflight.json", data)
        elif kind == "result":
            self.result = data

    def process_error(self, error):
        if error == QProcess.FailedToStart:
            self.result = {
                "status": "failed",
                "message": "Worker could not start. Check the application installation.",
            }
            self.finished(-1, QProcess.CrashExit)

    def finished(self, code, status):
        if not self.proc:
            return
        self.read_wire()
        self.read_logs()
        result = self.result or {
            "status": "interrupted",
            "message": "Worker exited unexpectedly. Device was not automatically reset.",
        }
        if code != 0 and result["status"] in ("success", "ready"):
            result = {
                "status": "failed",
                "message": f"Worker exited abnormally ({code}); its success response was discarded.",
            }
        self.store.finish(self.task_dir, result)
        self.proc.deleteLater()
        self.proc = None
        if self.action == "enter_edl":
            self.update(devices=[], deviceIndex=-1, capacities={})
        if result["status"] not in ("ready", "success") or self.action in (
            "flash",
            "reset",
        ):
            self.update(capacities={})
        self.update(
            busy=False,
            state=result["status"],
            message=result["message"],
            result=result,
            history=self.store.history(),
        )

    @Slot()
    def stop(self):
        if self.proc and self._data["state"] != "stopping":
            self.proc.write(b'{"version":1,"action":"stop"}\n')
            self.update(
                state="stopping",
                message="Stop requested. Waiting for the current operation boundary…",
            )

    def tick(self):
        if not self.proc:
            return
        now = time.monotonic()
        self.update(elapsed=int(now - self.started))
        phase = self._data["state"]
        limit = (
            30
            if phase == "connecting"
            else (
                600
                if self.action in ("discover", "validate") or phase == "checking"
                else 120
            )
        )
        elapsed = (
            now - self.phase_started if phase == "connecting" else now - self.activity
        )
        if elapsed > limit:
            self.result = {
                "status": "interrupted",
                "message": f"{phase.capitalize()} timed out. Worker terminated; inspect the device before retrying. No reset was sent.",
            }
            self.proc.kill()

    @Slot()
    def export(self):
        path, _ = QFileDialog.getSaveFileName(
            None,
            "Export diagnostics",
            str(Path.home() / "Desktop/QualcommFlashTool-diagnostics.zip"),
            "ZIP (*.zip)",
        )
        if path:
            try:
                self.store.export(path)
                self.update(message=f"Diagnostics saved to {path}")
            except Exception as exc:
                self.update(message=f"Export failed: {exc}")

    def close(self):
        if self.proc:
            self.proc.kill()
            self.proc.waitForFinished(3000)

    def demo(self):
        self.update(
            folder="/Firmware/Qualcomm Reference Package",
            loader="/Firmware/Qualcomm Reference Package/prog_firehose_ddr.elf",
            loaders=["/Firmware/Qualcomm Reference Package/prog_firehose_ddr.elf"],
            devices=[
                {
                    "id": "demo",
                    "name": "Qualcomm HS-USB QDLoader 9008",
                    "mode": "edl",
                    "status": "ready",
                    "serial": "DEMO · No hardware access",
                }
            ],
            deviceIndex=0,
            raws=["/Firmware/rawprogram0.xml", "/Firmware/rawprogram1.xml"],
            patches=["/Firmware/patch0.xml", "/Firmware/patch1.xml"],
            selectedRaws=["/Firmware/rawprogram0.xml", "/Firmware/rawprogram1.xml"],
            selectedPatches=["/Firmware/patch0.xml", "/Firmware/patch1.xml"],
            message="Demo preview · no device commands are executed.",
            environment=[
                {"name": "ADB", "ok": True, "detail": "Bundled runtime"},
                {"name": "USB runtime", "ok": True, "detail": "libusb ready"},
                {
                    "name": "EDL baseline",
                    "ok": True,
                    "detail": "3.62 · controlled adapter",
                },
            ],
        )
