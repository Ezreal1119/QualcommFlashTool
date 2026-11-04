import json
import os
import time
import uuid
import zipfile
from .runtime import data_dir


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(path)


class Store:
    def __init__(self):
        self.root = data_dir()
        self.tasks = self.root / "tasks"
        self.tasks.mkdir(exist_ok=True)
        self.settings = {
            "language": "en",
            "theme": "system",
            "reboot": True,
            "last_folder": "",
        }
        try:
            loaded = json.loads((self.root / "settings.json").read_text())
            if isinstance(loaded, dict):
                if loaded.get("language") in ("en", "zh"):
                    self.settings["language"] = loaded["language"]
                if loaded.get("theme") in ("system", "light", "dark"):
                    self.settings["theme"] = loaded["theme"]
                if type(loaded.get("reboot")) is bool:
                    self.settings["reboot"] = loaded["reboot"]
                if isinstance(loaded.get("last_folder"), str):
                    self.settings["last_folder"] = loaded["last_folder"]
        except (OSError, ValueError):
            pass
        for path in self.tasks.glob("*/record.json"):
            try:
                record = json.loads(path.read_text())
                if not isinstance(record, dict):
                    continue
                if record.get("status") == "running":
                    record.update(
                        status="interrupted",
                        message="Application exited before this task completed. No automatic resume.",
                        ended=time.time(),
                    )
                    atomic_json(path, record)
            except (OSError, ValueError):
                pass

    def save_settings(self):
        atomic_json(self.root / "settings.json", self.settings)

    def begin(self, action, request):
        task_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        folder = self.tasks / task_id
        folder.mkdir()
        atomic_json(
            folder / "record.json",
            {
                "id": task_id,
                "action": action,
                "started": time.time(),
                "status": "running",
                "device": request.get("device"),
                "plan": request.get("plan"),
            },
        )
        return folder

    def finish(self, folder, result):
        path = folder / "record.json"
        record = json.loads(path.read_text())
        record.update(result)
        record["ended"] = time.time()
        atomic_json(path, record)

    def history(self):
        result = []
        for p in sorted(self.tasks.glob("*/record.json"), reverse=True):
            try:
                record = json.loads(p.read_text())
                if not isinstance(record, dict):
                    continue
                record.pop("plan", None)
                result.append(record)
            except (OSError, ValueError):
                pass
        return result

    def export(self, destination, task_id=""):
        with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
            chosen = (
                [self.tasks / task_id]
                if task_id
                else sorted(self.tasks.iterdir())[-20:]
            )
            for folder in chosen:
                for path in folder.glob("*"):
                    if path.is_file():
                        archive.write(path, str(path.relative_to(self.root)))
            archive.writestr(
                "README.txt",
                "Qualcomm Flash Tool internal diagnostics. Contains local paths and device identifiers. No firmware images or credentials are included.\n",
            )
