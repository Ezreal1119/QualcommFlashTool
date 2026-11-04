import json
import subprocess
import sys
import zipfile
from qft.storage import Store


def worker(request):
    p = subprocess.run(
        [sys.executable, "-m", "qft.app", "--worker"],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert p.returncode == 0
    return [json.loads(line) for line in p.stdout.splitlines()]


def test_worker_version_rejection():
    events = worker({"version": 99, "action": "scan"})
    assert events[-1]["data"]["status"] == "failed"


def test_worker_discovery_and_validation(package):
    events = worker({"version": 1, "action": "discover", "folder": str(package)})
    assert events[0]["kind"] == "package"
    result = worker(
        {
            "version": 1,
            "action": "validate",
            "folder": str(package),
            "loader": str(package / "prog_firehose.mbn"),
            "memory": "emmc",
            "raws": [str(package / "rawprogram0.xml")],
            "patches": [str(package / "patch0.xml")],
        }
    )
    assert [e["kind"] for e in result] == ["plan", "result"]
    assert result[-1]["data"]["status"] == "ready"


def test_storage_crash_recovery_and_export(tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    store = Store()
    task = store.begin(
        "flash", {"device": {"name": "test"}, "plan": {"folder": "/firmware"}}
    )
    (task / "raw.log").write_text("local log")
    recovered = Store()
    assert recovered.history()[0]["status"] == "interrupted"
    archive = tmp_path / "report.zip"
    recovered.export(archive)
    with zipfile.ZipFile(archive) as z:
        assert any(p.endswith("/record.json") for p in z.namelist())
        assert any(p.endswith("/raw.log") for p in z.namelist())


def test_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path))
    store = Store()
    store.settings["language"] = "zh"
    store.save_settings()
    assert Store().settings["language"] == "zh"


def test_unknown_action_cannot_fall_through_to_flashing():
    events = worker({"version": 1, "action": "typo"})
    assert events[-1]["data"]["status"] == "failed"
    assert "Unsupported worker action" in events[-1]["data"]["message"]


def test_corrupt_settings_fall_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text('["invalid"]')
    assert Store().settings["language"] == "en"
    (tmp_path / "settings.json").write_text(
        '{"language":"unknown","theme":"unknown","reboot":"false"}'
    )
    store = Store()
    assert (
        store.settings["language"] == "en"
        and store.settings["theme"] == "system"
        and store.settings["reboot"] is True
    )
