# Qualcomm Flash Tool

Compact, English-only Qualcomm flasher for **Apple Silicon · macOS 13+**.

## Use

1. Open the DMG and drag the app into Applications. No Python installation needed.
2. Connect one device. Choose an **extracted firmware folder** and confirm it.
3. Wait for checks, then **Flash… → Flash & reboot**. Reboot follows successful verification.

**Configure…** lets you change the loader, XML and image files. **History** contains task records and diagnostic export. Use firmware intended for your device; checks do not establish model compatibility.

## Build on another Mac

Requires [uv](https://docs.astral.sh/uv/) and Xcode command-line tools (`xcode-select --install`). From the cloned project directory, run:

```sh
scripts/build.sh
```

The script automatically prepares the pinned ADB/libusb when missing, installs Python/dependencies, runs checks and produces **`dist/QualcommFlashTool.app` + `dist/*.dmg`**. Internet access is needed. Your system/Homebrew ADB is unchanged; no manual ADB path or downgrade is needed.

## Run from source

```sh
UV_PROJECT_ENVIRONMENT=.app-venv uv sync --frozen --extra dev --python 3.11.13 --managed-python
.app-venv/bin/python main.py
```

## Distribution & details

- Commit/push the source before pulling it on another Mac. `dist/` and `build/` are ignored by Git; share the built DMG separately.
- Current builds are internally signed and **not Apple-notarized**.
- [Detailed guide](docs/guide.md) · [DT50 hardware test](docs/hardware/DT50-2026-11-12.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)
