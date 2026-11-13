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
from PySide6.QtGui import QDesktopServices
from .models import PROTOCOL_VERSION
from .runtime import root
from .storage import Store, atomic_json

CANCELLABLE_ACTIONS = {
    "environment",
    "prepare",
    "discover",
    "validate",
    "scan",
    "inspect",
}
PROTECTED_ACTIONS = {"flash", "reset", "enter_edl", "one_click"}


class Controller(QObject):
    changed = Signal()
    editorRequested = Signal()
    folderConfirmationRequested = Signal()
    quitRequested = Signal()

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
        self._auto_check = False
        self._generation = 0
        self._cancel_requested = False
        self._inspected_device = None
        self._editing_hash = None
        self._pending_source = None
        self._quit_requested = False
        self._import_workspace = None
        self._data = {
            "page": 0,
            "advanced": False,
            "packageStatus": "waiting",
            "deviceStatus": "waiting",
            "packageIssues": [],
            "packageSource": "",
            "folderToConfirm": "",
            "memorySource": "auto",
            "canFlash": False,
            "canStart": False,
            "deviceNotice": "missing",
            "canCancelNow": False,
            "canChoosePackage": True,
            "closeBlocked": False,
            "editorPath": "",
            "editorText": "",
            "editorError": "",
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
            "layoutNotes": [],
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
            "reboot": True,
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
        self._data["canCancelNow"] = bool(
            self._data["busy"] and self.action in CANCELLABLE_ACTIONS
        )
        self._data["canChoosePackage"] = not self._quit_requested and (
            not self._data["busy"] or self._data["canCancelNow"]
        )
        self._data["closeBlocked"] = bool(
            self._data["busy"] and self.action in PROTECTED_ACTIONS
        )
        device = self.device()
        self._data["canFlash"] = bool(
            not self._data["busy"]
            and self._data["plan"]
            and self._data["capacities"]
            and device
            and device["mode"] == "edl"
            and device["id"] == self._inspected_device
            and not self._data["editorPath"]
        )
        if device is None:
            notice = "choose" if self._data["devices"] else "missing"
        elif device["mode"] == "edl":
            notice = "edl_ready"
        elif device["status"] == "unauthorized":
            notice = "unauthorized"
        elif device["status"] == "offline":
            notice = "offline"
        elif device["status"] == "device":
            notice = "adb_ready" if device.get("usb_path") else "usb_identity_missing"
        else:
            notice = "unavailable"
        self._data["deviceNotice"] = notice
        self._data["canStart"] = bool(
            not self._data["busy"]
            and self._data["packageStatus"] == "passed"
            and self._data["plan"]
            and device
            and not self._data["editorPath"]
            and not self._data["folderToConfirm"]
            and (
                self._data["canFlash"]
                or (
                    device["mode"] == "adb"
                    and device["status"] == "device"
                    and device.get("usb_path")
                )
            )
        )
        self.changed.emit()

    def invalidate(self):
        self._generation += 1
        self._auto_check = False
        self._inspected_device = None
        self.update(
            packageStatus="changed",
            deviceStatus="waiting",
            packageIssues=[],
            plan={},
            capacities={},
            warnings=[],
            layoutNotes=[],
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
        if key == "advanced":
            self.update(advanced=bool(value))
        elif key in ("theme", "reboot"):
            self.store.settings[key] = value
            self.store.save_settings()
            self.update(**{key: value})
        elif key == "memory" and value in ("ufs", "emmc"):
            self.invalidate()
            self.update(
                memory=value,
                memorySource="manual",
                message="Configuration changed. Run Check again before flashing.",
            )

    @Slot(int)
    def selectDevice(self, index):
        if self.editable():
            if not -1 <= index < len(self._data["devices"]):
                return
            self._inspected_device = None
            self.update(
                deviceIndex=index, capacities={}, result={}, deviceStatus="waiting"
            )
            if self._data["plan"] and index >= 0:
                self._auto_check = True
                self._continue_auto("scan")

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
        if not self._data["canChoosePackage"]:
            return
        if self.proc:
            self.stop()
        folder = QFileDialog.getExistingDirectory(
            None,
            "Choose extracted firmware",
            self.store.settings.get("last_folder", ""),
        )
        if folder:
            self.requestFolder(folder)

    @Slot(str)
    def dropFolder(self, url):
        if self._data["canChoosePackage"]:
            self.requestFolder(QUrl(url).toLocalFile())

    def requestFolder(self, folder):
        if not self._data["canChoosePackage"]:
            return
        path = Path(folder)
        if not path.is_dir():
            self.update(
                message="Only extracted firmware folders are accepted. ZIP files cannot be selected."
            )
            return
        self.update(folderToConfirm=str(path.resolve()))
        self.folderConfirmationRequested.emit()

    @Slot()
    def confirmFolder(self):
        folder = self._data["folderToConfirm"]
        self.update(folderToConfirm="")
        if folder:
            self.loadFolder(folder)

    @Slot()
    def cancelFolderSelection(self):
        self.update(folderToConfirm="")

    @Slot(str)
    def loadFolder(self, folder):
        if not self._data["canChoosePackage"]:
            return
        if not Path(folder).is_dir():
            self.update(
                message="Select an extracted firmware folder. ZIP import is disabled."
            )
            return
        if self.proc:
            self._pending_source = folder
            self.stop()
            return
        self.invalidate()
        self._auto_check = True
        self.update(
            packageSource=folder,
            packageStatus="checking",
            deviceStatus="waiting",
            advanced=False,
            folder=folder if Path(folder).is_dir() else "",
            loaders=[],
            loader="",
            raws=[],
            patches=[],
            selectedRaws=[],
            selectedPatches=[],
        )
        self.store.settings["last_folder"] = str(
            Path(folder) if Path(folder).is_dir() else Path(folder).parent
        )
        self.store.save_settings()
        self.launch("prepare", {"source": folder})

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
            self._auto_check = bool(self._data["plan"])
            self.update(deviceStatus="checking")
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
        self._auto_check = True
        self.update(packageStatus="checking")
        self.launch(
            "validate",
            {
                k: self._data[v]
                for k, v in [
                    ("folder", "folder"),
                    ("loader", "loader"),
                    ("memory", "memory"),
                    ("memory_source", "memorySource"),
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
        if action == "flash" and not self._data["canFlash"]:
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

    @Slot()
    def startFlash(self):
        """Only called after the user confirms the target and complete workflow."""
        if not self._data["canStart"]:
            self.update(
                message="Connect an authorized USB device, select it, and validate the firmware before starting."
            )
            return
        self._auto_check = False
        self.launch(
            "one_click",
            {
                "device": self.device(),
                "plan": self._data["plan"],
                "reboot": True,
                "capacities": self._data["capacities"],
            },
        )

    def launch(self, action, payload):
        if self.proc or self._data["demo"]:
            return
        self.action = action
        self._cancel_requested = False
        self._import_workspace = None
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
        if kind == "import_workspace":
            self._import_workspace = data["path"]
            return
        if self._cancel_requested and self.action in CANCELLABLE_ACTIONS:
            return  # Discard late package/plan/results from the cancelled worker.
        if kind == "scan_progress":
            self.activity = time.monotonic()
            self.update(
                message=f"Scanning firmware folder… {data['entries']} entries checked.",
                currentFile=data["path"],
            )
            return
        if kind in ("activity", "progress"):
            self.activity = time.monotonic()
        if kind == "connection":
            detail = (
                f" · LUN {data['physical_partition_number']}"
                if "physical_partition_number" in data
                else ""
            )
            self.log(f"[{time.strftime('%H:%M:%S')}] USB · {data['step']}{detail}\n")
        elif kind == "stage":
            self.phase_started = self.activity = time.monotonic()
            detail = data.get("label") or data.get("file", "")
            self.log(
                f"[{time.strftime('%H:%M:%S')}] {data['state'].upper()}"
                + (f" · {detail}" if detail else "")
                + "\n"
            )
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
        elif kind == "device_bound":
            device = data["device"]
            self._inspected_device = None
            self.update(
                devices=[device], deviceIndex=0, capacities={}, deviceStatus="checking"
            )
            if self.task_dir:
                atomic_json(self.task_dir / "device-binding.json", data)
        elif kind == "devices":
            previous = self.device()
            devices = data["devices"]
            edl = [i for i, d in enumerate(devices) if d["mode"] == "edl"]
            selected = -1
            if previous:
                selected = next(
                    (i for i, d in enumerate(devices) if d["id"] == previous["id"]), -1
                )
            if selected < 0 and len(edl) == 1:
                selected = edl[0]
            if selected < 0 and not edl and len(devices) == 1:
                selected = 0
            self._inspected_device = None
            self.update(
                devices=devices,
                deviceIndex=selected,
                capacities={},
                deviceStatus="waiting",
                warnings=data["errors"],
            )
        elif kind == "environment":
            self.update(environment=data["checks"])
        elif kind == "package":
            from .firmware import automatic_configuration

            # Legacy discover events are still supported by the internal protocol.
            if "issues" not in data:
                data = automatic_configuration(self._data["folder"])
            issues = data["issues"]
            self.update(
                folder=data["folder"],
                loaders=data["loaders"],
                loader=data["loader"],
                raws=data["raws"],
                patches=data["patches"],
                selectedRaws=data["selectedRaws"],
                selectedPatches=data["selectedPatches"],
                memory=data["memoryHint"],
                memorySource="auto",
                packageIssues=issues,
                advanced=bool(issues),
                packageStatus="attention" if issues else "checking",
            )
        elif kind == "import_progress":
            self.update(
                message="Extracting firmware ZIP…",
                currentFile=f"{data['done'] // 1048576} / {data['total'] // 1048576} MB",
            )
        elif kind == "plan":
            from .models import FlashPlan
            from .package import layout_notes

            notes = layout_notes(FlashPlan.from_dict(data))
            self.update(
                layoutNotes=notes,
                plan=data,
                memory=data["memory"],
                packageStatus="passed",
                packageIssues=[],
                capacities={},
                warnings=notes
                + ["Device model / firmware compatibility has not been established."],
            )
        elif kind == "preflight":
            device = self.device()
            self._inspected_device = device["id"] if device else None
            self.update(
                deviceStatus="passed",
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
        cancelled_check = self._cancel_requested and self.action in CANCELLABLE_ACTIONS
        if cancelled_check:
            result = {
                "status": "interrupted",
                "message": "Checks stopped. Nothing was flashed.",
            }
        self.log(
            f"\n[{time.strftime('%H:%M:%S')}] {result['status'].upper()} · {result['message']}\n"
        )
        for item in result.get("verified", []):
            self.log(f"  PASS · {item}\n")
        for item in result.get("unverified", []):
            self.log(f"  NOT VERIFIED · {item}\n")
        completed_action = self.action
        self.store.finish(self.task_dir, result)
        self.proc.deleteLater()
        self.proc = None
        if completed_action == "enter_edl":
            self.update(devices=[], deviceIndex=-1, capacities={})
        if result["status"] not in ("ready", "success") or self.action in (
            "flash",
            "reset",
            "one_click",
        ):
            self.update(capacities={})
        ui_state = result["status"]
        if ui_state == "success" and completed_action not in (
            "flash",
            "one_click",
            "reset",
        ):
            target = self.device()
            ui_state = (
                "ready"
                if self._data["plan"]
                and target
                and target["mode"] == "adb"
                and target["status"] == "device"
                and target.get("usb_path")
                else "idle"
            )
        self.update(
            busy=False,
            state=ui_state,
            message=result["message"],
            result=result,
            history=self.store.history(),
        )
        if completed_action == "one_click":
            if result["status"] != "success":
                self.update(deviceStatus="attention", devices=[], deviceIndex=-1)
            elif result.get("rebooted"):
                self.update(devices=[], deviceIndex=-1, deviceStatus="waiting")
        if cancelled_check:
            self._auto_check = False
            self._inspected_device = None
            self.update(
                plan={},
                capacities={},
                packageStatus="changed" if self._data["folder"] else "waiting",
                deviceStatus="waiting",
                packageIssues=[],
                advanced=False,
            )
            self._cleanup_cancelled_import()
            if self._quit_requested:
                self.quitRequested.emit()
            elif self._pending_source:
                source, self._pending_source = self._pending_source, None
                self.loadFolder(source)
            return
        if self._quit_requested:
            self.quitRequested.emit()
            return
        if result["status"] in ("failed", "interrupted", "attention"):
            self._auto_check = False
            if completed_action in ("prepare", "validate"):
                self.update(
                    packageStatus="attention",
                    advanced=True,
                    packageIssues=self._data["packageIssues"] or [result["message"]],
                )
            elif completed_action in ("inspect", "scan"):
                self.update(deviceStatus="attention")
        elif completed_action == "enter_edl":
            generation = self._generation
            QTimer.singleShot(
                1500,
                lambda: self.refresh()
                if generation == self._generation and self.editable()
                else None,
            )
        elif self._auto_check:
            self._continue_auto(completed_action)

    def _continue_auto(self, action):
        if not self._auto_check or self.proc or not self.editable():
            return
        if action in ("prepare", "validate") and self._data["plan"]:
            self.update(deviceStatus="checking")
            self.launch("scan", {})
        elif action == "scan":
            edl = [d for d in self._data["devices"] if d["mode"] == "edl"]
            device = self.device()
            if len(edl) > 1:
                self._auto_check = False
                self.update(
                    deviceStatus="attention",
                    message="Multiple EDL devices found. Connect only the device you want to flash.",
                )
            elif device and device["mode"] == "edl" and self._data["plan"]:
                self.update(
                    deviceStatus="checking",
                    message="Checking device using the selected loader. Firmware will not be written.",
                )
                self.run("inspect")
            elif self._data["warnings"] and not edl:
                self._auto_check = False
                self.update(
                    deviceStatus="attention", message="\n".join(self._data["warnings"])
                )
            else:
                self._auto_check = False
                from .devices import connection_message

                self.update(
                    deviceStatus="waiting",
                    message=connection_message(
                        {"devices": self._data["devices"], "errors": []}
                    ),
                )
        elif action == "inspect":
            self._auto_check = False
            self.update(
                deviceStatus="passed",
                message="File and device checks passed. Review the target and confirm flashing. Device-model compatibility is not established automatically.",
            )

    @Slot()
    def check(self):
        if not self.editable() or self._data["editorPath"]:
            return
        if not self._data["folder"]:
            self.update(message="Choose firmware first.")
            return
        if (
            not self._data["loader"]
            or not self._data["selectedRaws"]
            or not self._data["selectedPatches"]
        ):
            self.update(
                advanced=True,
                message="Complete the file choices in Advanced configuration first.",
            )
            return
        self.validate()

    @Slot()
    def openFolder(self):
        if self._data["folder"]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._data["folder"]))

    @Slot(str)
    def editXml(self, path):
        if not self.editable() or self._data["demo"]:
            return
        if path not in self._data["raws"] + self._data["patches"]:
            return
        from .xml_editor import read_xml

        try:
            text, self._editing_hash = read_xml(path)
            self._auto_check = False
            self.update(editorPath=path, editorText=text, editorError="")
            self.editorRequested.emit()
        except Exception as exc:
            self.update(message=f"Cannot open XML: {exc}")

    @Slot(str, result=bool)
    def saveXml(self, text):
        if not self.editable() or not self._data["editorPath"]:
            return False
        from .xml_editor import save_xml

        try:
            backup = save_xml(self._data["editorPath"], text, self._editing_hash)
            self.update(editorPath="", editorText="", editorError="")
            self.invalidate()
            self.log(f"XML saved. Original backed up to {backup}\n")
            self.check()
            return True
        except Exception as exc:
            self.update(editorError=str(exc))
            return False

    @Slot()
    def cancelEdit(self):
        self.update(editorPath="", editorText="", editorError="")

    @Slot()
    def clearPackage(self):
        if not self._data["canChoosePackage"]:
            return
        self._pending_source = None
        if self.proc:
            self.stop()
        if not self.proc:
            self.invalidate()
        else:
            self.update(plan={}, capacities={}, result={})
        self.update(
            folder="",
            packageSource="",
            loaders=[],
            loader="",
            raws=[],
            patches=[],
            selectedRaws=[],
            selectedPatches=[],
            packageStatus="waiting",
            currentFile="",
            message="Choose a firmware folder to begin.",
        )

    @Slot()
    def quitAfterStop(self):
        self._quit_requested = True
        self.update()
        self._pending_source = None
        if self.proc:
            self.stop()
        else:
            self.quitRequested.emit()

    def _cleanup_cancelled_import(self):
        if self.action != "prepare" or not self._import_workspace:
            return
        from .runtime import data_dir
        import shutil
        import threading

        path = Path(self._import_workspace)
        # Only delete this worker's own newly created ZIP working copy.
        if path.parent.resolve() == (
            data_dir() / "packages"
        ).resolve() and path.name.startswith("firmware-"):
            threading.Thread(
                target=lambda: shutil.rmtree(path, ignore_errors=True), daemon=True
            ).start()
        self._import_workspace = None

    @Slot()
    def stop(self):
        if not self.proc or self._data["state"] == "stopping":
            return
        self._auto_check = False
        self._generation += 1
        self._cancel_requested = True
        proc = self.proc
        proc.write(b'{"version":1,"action":"stop"}\n')
        if self.action in CANCELLABLE_ACTIONS:
            self._inspected_device = None
            self.update(
                state="stopping",
                capacities={},
                message="Cancelling checks… You can choose another package or close the window.",
            )
            # Cooperative cleanup gets a short grace period. A stuck filesystem
            # call must never keep the UI locked waiting for the worker.
            QTimer.singleShot(
                300,
                lambda: proc.kill()
                if self.proc is proc and self._cancel_requested
                else None,
            )
        else:
            self.update(
                state="stopping",
                message="Stop requested. Waiting for the current device operation boundary…",
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
                if self.action in ("prepare", "discover", "validate")
                or phase == "checking"
                else 120
            )
        )
        if phase == "waiting_edl":
            limit = 75
        elapsed = (
            now - self.phase_started
            if phase in ("connecting", "waiting_edl")
            else now - self.activity
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
            packageStatus="passed",
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
