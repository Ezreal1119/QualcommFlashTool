import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication
from qft.controller import Controller
from qft.firmware import automatic_configuration, import_firmware
from qft.xml_editor import read_xml, save_xml
from qft.expressions import ValidationError


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_zip(package, destination):
    with zipfile.ZipFile(destination, "w") as z:
        for path in package.iterdir():
            z.write(path, "Firmware/" + path.name)
    return destination


def test_automatic_package_defaults(package):
    config = automatic_configuration(package)
    assert not config["issues"] and config["memoryHint"] == "emmc"
    assert config["loader"].endswith("prog_firehose.mbn")
    assert len(config["selectedRaws"]) == len(config["selectedPatches"]) == 1


def test_ufs_memory_hint(package):
    raw = package / "rawprogram0.xml"
    raw.write_text(raw.read_text().replace("512", "4096"))
    assert automatic_configuration(package)["memoryHint"] == "ufs"


def test_invalid_binary_is_excluded_with_reason(package):
    (package / "prog_other.elf").write_bytes(b"other")
    config = automatic_configuration(package)
    assert not config["issues"] and config["loader"].endswith("prog_firehose.mbn")
    assert any(r["status"] == "excluded" for r in config["loaderReports"])


def test_ui_recheck_preserves_auto_pool_and_manual_selection_narrows_it(
    app, package, tmp_path, monkeypatch
):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "appdata"))
    c = Controller(demo=True)
    launches = []
    monkeypatch.setattr(
        c, "launch", lambda action, payload: launches.append((action, payload))
    )
    c.handle("package", automatic_configuration(package))
    c.validate()
    assert launches[-1][1]["loader_candidates"] == [str(package / "prog_firehose.mbn")]
    c.selectLoader(str(package / "prog_firehose.mbn"))
    c.validate()
    assert launches[-1][1]["loader_candidates"] == []
    c.timer.stop()


def test_duplicate_raw_variants_are_never_guessed(package):
    (package / "rawprogram_unsparse0.xml").write_text(
        (package / "rawprogram0.xml").read_text()
    )
    config = automatic_configuration(package)
    assert config["issues"] and not config["selectedRaws"]


def test_missing_patch_needs_review(package):
    (package / "patch0.xml").unlink()
    assert automatic_configuration(package)["issues"]


def test_import_zip_editable_copy_preserves_archive(package, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "appdata"))
    archive = make_zip(package, tmp_path / "firmware.zip")
    original = archive.read_bytes()
    folder = import_firmware(archive)
    config = automatic_configuration(folder)
    assert config["memoryHint"] == "emmc" and not config["issues"]
    xml = Path(config["raws"][0])
    text, digest = read_xml(xml)
    backup = save_xml(xml, text.replace('label="boot"', 'label="boot_a"'), digest)
    assert Path(backup).read_text() == text
    assert archive.read_bytes() == original
    # Reimporting never destroys edits in the previous working copy.
    assert import_firmware(archive) != folder
    assert "boot_a" in xml.read_text()


@pytest.mark.parametrize(
    "name",
    [
        "../outside.xml",
        "/absolute.xml",
        "C:/absolute.xml",
        "sub/../../outside.xml",
        "a/../outside.xml",
    ],
)
def test_unsafe_zip_paths_rejected(tmp_path, monkeypatch, name):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "appdata"))
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(name, b"bad")
    with pytest.raises(ValidationError):
        import_firmware(archive)
    assert list((tmp_path / "appdata/packages").iterdir()) == []


def test_zip_symlink_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "appdata"))
    archive = tmp_path / "bad.zip"
    entry = zipfile.ZipInfo("link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(entry, "/tmp")
    with pytest.raises(ValidationError):
        import_firmware(archive)


def test_case_colliding_zip_entries_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "appdata"))
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("BOOT.img", b"A")
        z.writestr("boot.img", b"B")
    with pytest.raises(ValidationError):
        import_firmware(archive)


def test_cancelled_import_cleans_working_copy(package, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "appdata"))
    archive = make_zip(package, tmp_path / "firmware.zip")
    with pytest.raises(ValidationError):
        import_firmware(archive, stopped=lambda: True)
    assert list((tmp_path / "appdata/packages").iterdir()) == []


def test_xml_backup_and_conflict_detection(package):
    path = package / "rawprogram0.xml"
    text, digest = read_xml(path)
    backup = save_xml(
        path, text.replace('start_sector="10"', 'start_sector="12"'), digest
    )
    assert Path(backup).read_text() == text
    with pytest.raises(ValidationError, match="changed"):
        save_xml(path, text, digest)
    assert 'start_sector="12"' in path.read_text()


def test_invalid_xml_never_overwrites_original(package):
    path = package / "rawprogram0.xml"
    text, digest = read_xml(path)
    with pytest.raises(Exception):
        save_xml(path, "<data><program", digest)
    assert path.read_text() == text
    assert not list(package.glob("*.qft-backup-*"))


def test_zip_prepare_worker_is_rejected(package, tmp_path):
    archive = make_zip(package, tmp_path / "firmware.zip")
    request = {"version": 1, "action": "prepare", "source": str(archive)}
    p = subprocess.run(
        [sys.executable, "-m", "qft.app", "--worker"],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        env={**os.environ, "QFT_DATA_DIR": str(tmp_path / "data")},
        timeout=15,
    )
    events = [json.loads(line) for line in p.stdout.splitlines()]
    assert p.returncode == 0 and events[-1]["data"]["status"] == "failed"
    assert "ZIP import is disabled" in events[-1]["data"]["message"]
    assert not any(e["kind"] == "plan" for e in events)
    assert not any(e["kind"] in ("preflight", "devices") for e in events)


def test_auto_chain_inspects_but_never_flashes(app, tmp_path, monkeypatch, plan):
    from dataclasses import asdict

    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    c = Controller(demo=True)
    calls = []
    c.update(demo=False, plan=asdict(plan), packageStatus="passed")
    monkeypatch.setattr(
        c, "launch", lambda action, payload: calls.append((action, payload))
    )
    c._auto_check = True
    c._continue_auto("prepare")
    assert calls[-1][0] == "scan"
    dev = {
        "id": "usb:1:2",
        "mode": "edl",
        "name": "Test",
        "status": "ready",
        "serial": "1",
        "bus": 1,
        "address": 2,
    }
    c.handle("devices", {"devices": [dev], "errors": []})
    c._continue_auto("scan")
    assert c.device()["id"] == dev["id"] and calls[-1][0] == "inspect"
    c.task_dir = tmp_path
    c.update(busy=True)
    c.handle(
        "preflight",
        {"capacities": {0: 128}, "warnings": [], "identity_match": "Unknown model"},
    )
    assert not c.data["canFlash"]
    c.update(busy=False)
    c._continue_auto("inspect")
    assert c.data["canFlash"] and all(a != "flash" for a, p in calls)
    c.timer.stop()


def test_multiple_edl_devices_block_auto_inspection(app, tmp_path, monkeypatch, plan):
    from dataclasses import asdict

    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    c = Controller(demo=True)
    calls = []
    c.update(demo=False, plan=asdict(plan))
    monkeypatch.setattr(c, "launch", lambda action, payload: calls.append(action))
    devices = [
        {
            "id": f"usb:1:{n}",
            "name": "Test",
            "mode": "edl",
            "status": "ready",
            "serial": "",
        }
        for n in (1, 2)
    ]
    c.handle("devices", {"devices": devices, "errors": []})
    c._auto_check = True
    c._continue_auto("scan")
    assert (
        not calls and not c.data["canFlash"] and c.data["deviceStatus"] == "attention"
    )
    c.timer.stop()


def test_manual_changes_and_editor_invalidate_readiness(
    app, tmp_path, monkeypatch, plan
):
    from dataclasses import asdict

    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    c = Controller(demo=True)
    c.update(demo=False, plan=asdict(plan), capacities={"0": 128})
    c._inspected_device = "demo"
    c.update()
    assert c.data["canFlash"]
    c.selectLoader(plan.loader)
    assert (
        not c.data["canFlash"]
        and not c.data["plan"]
        and c.data["packageStatus"] == "changed"
    )
    c.update(
        raws=list(plan.raw_files),
        patches=list(plan.patch_files),
        plan=asdict(plan),
        capacities={"0": 128},
    )
    c._inspected_device = "demo"
    c.update()
    c.editXml(plan.raw_files[0])
    assert c.data["editorPath"] and not c.data["canFlash"]
    monkeypatch.setattr(c, "check", lambda: None)
    assert c.saveXml(c.data["editorText"].replace('label="boot"', 'label="boot_a"'))
    assert not c.data["plan"] and not c.data["capacities"]
    c.timer.stop()


def test_cancelled_checks_do_not_continue_pipeline(app, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    c = Controller(demo=True)
    calls = []
    c.update(demo=False, busy=True)
    c.task_dir = c.store.begin("validate", {})
    c.proc = SimpleNamespace(deleteLater=lambda: None)
    c.action = "validate"
    c.result = {"status": "ready", "message": "checked"}
    c._auto_check = True
    c._cancel_requested = True
    monkeypatch.setattr(c, "read_wire", lambda: None)
    monkeypatch.setattr(c, "read_logs", lambda: None)
    monkeypatch.setattr(c, "launch", lambda action, payload: calls.append(action))
    c.finished(0, None)
    assert c.data["state"] == "interrupted" and not calls
    c.timer.stop()


def test_mac_zip_metadata_does_not_create_loader_ambiguity(package):
    metadata = package / "__MACOSX"
    metadata.mkdir()
    (metadata / "._prog_firehose.mbn").write_bytes(b"metadata")
    (package / "._prog_firehose.mbn").write_bytes(b"metadata")
    config = automatic_configuration(package)
    assert not config["issues"] and len(config["loaders"]) == 1
