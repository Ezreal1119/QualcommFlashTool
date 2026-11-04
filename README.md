# Qualcomm Flash Tool 2.0

A PySide6 / QML desktop application for macOS 13+ on Apple Silicon. This build is for internal use. Physical-device compatibility is **not yet validated**.

## Run

Install [uv](https://docs.astral.sh/uv/) and use the pinned Python runtime:

```sh
uv python install 3.11.13
UV_PROJECT_ENVIRONMENT=.app-venv uv sync --frozen --extra dev --python 3.11.13 --managed-python
.app-venv/bin/python main.py
```

`--demo` opens a disconnected visual preview; device commands are disabled. `--screenshot /absolute/path.png` captures a window and exits. `QT_QPA_PLATFORM=offscreen` enables headless previews. The initial environment check loads libraries only; device discovery is explicitly requested with Refresh.

## Use

1. Connect one device and refresh the device list. Authorize ADB on the device if needed. Enter EDL only through the explicit confirmation dialog.
2. Refresh and reselect the EDL device after changing modes. The app will not infer that an ADB device and an EDL USB device are the same device.
3. Import an **extracted** firmware directory. Choose a compatible ELF / MBN loader and XML files. Duplicate rawprogram variants must be resolved explicitly. Rawprogram and patch indices must match.
4. Validate the files, then inspect the selected EDL device. Inspection uploads the chosen loader into RAM and queries storage geometry; it does not flash firmware or reset the device.
5. Review the execution plan, including any overlapping writes and capacity information. Start Flash requires explicit confirmation and overwrites device storage.
6. Review the result. Failed or interrupted tasks do not reset or automatically retry the device. Incomplete verification also prevents automatic reboot.

The app does not establish device-model / firmware compatibility. Vendor authorization requirements remain in effect. Permission problems are reported without silently elevating privileges or changing sudoers.

## Supported package behavior

- UFS / eMMC; standard `<data><program .../></data>` and `<data><patch .../></data>` XML; raw and Android sparse images. Empty program filenames describe unprogrammed regions and generate no writes.
- Image paths resolve relative to their XML file and must remain inside the package directory. Loaders and manually selected XML may be in different directories; absolute paths are retained.
- Sector sizes 512 / 4096, file-sector offsets, bounded writes, final-sector zero padding, numeric LUN ordering and original operation order. All programs execute before patches. Mixed sector sizes, missing images and unsupported XML features are rejected.
- Sparse RAW / FILL / DONT_CARE / CRC chunks are validated; DONT_CARE ranges are expanded to zero. Sparse checksum verification occurs before device connection.
- XML arithmetic permits integer constants, NUM_DISK_SECTORS, +, -, *, exact division and CRC32(sector, byte_length) for patch values. There is no arbitrary expression execution.
- Capacity must be reported by the device's per-LUN getstorageinfo response. Unknown or inconsistent geometry blocks flashing. No capacity is guessed from a firmware image or an old partition table.
- Initial and final write acknowledgements, patch acknowledgements, final primary/backup GPT header and entry CRCs, plus final boot / vbmeta SHA-256 where advertised by the loader. Unavailable checks are explicitly listed. Overlapping writes and patches are included in expected digest reconstruction.
- A stop request takes effect between operations. Stalled workers are killed by the supervisor. No automatic resumption or rollback is claimed.

## Architecture

`qft/package.py` builds immutable, fingerprinted execution plans. `qft/engine.py` controls tasks independently of the UI. `qft/backend.py` uses pinned EDL primitives through a checked adapter; upstream qfil and its exit-code heuristics are not used. The QML controller talks to a separate worker over versioned JSON Lines. No UI thread performs USB transactions.

Files are rehashed before every device session. Protocol errors, missing ACKs, short transfers, disconnects and process crashes stop the task. There is no raw-write retry. The worker never implicitly resets a device on error.

Settings, task records, full logs and inspection reports are stored in `~/Library/Application Support/QualcommFlashTool`. A single-instance lock avoids competing task supervisors. Startup marks unfinished records as interrupted. Diagnostic exports include the last 20 task records, paths and device identifiers; no firmware images or credentials are included and nothing is uploaded. `QFT_DATA_DIR` can override this location for testing.

## Build an internal installer

Provide an official Android SDK `platform-tools` directory with ADB 36.0.0-13206524. The resource preparation script builds libusb 1.0.29 from its checksum-pinned release source for macOS 13; it requires Xcode command-line tools:

```sh
QFT_ADB_SOURCE=/path/to/platform-tools scripts/prepare_resources.sh
scripts/build.sh
```

Build outputs: `dist/QualcommFlashTool.app`, `dist/QualcommFlashTool-2.0.0-arm64.dmg`, SHA-256 sidecar. The app includes Python, Qt, ADB, libusb and the EDL snapshot; users do not need Python or Homebrew. Resource binaries and generated build directories are not tracked. Dependency versions are locked by `uv.lock`; the EDL baseline has a separate SHA-256 manifest.

Internal builds use local ad-hoc signing, not Apple notarization. They may require explicit user approval in macOS before opening. A future external release requires a licensing decision and Developer ID distribution credentials. `QFT_SIGN_IDENTITY` and optional `QFT_NOTARY_PROFILE` enable the signing/notarization script. No credentials are stored in this repository.

## Verification

```sh
.app-venv/bin/python -m pytest -q
.app-venv/bin/python scripts/verify_bundle.py dist/QualcommFlashTool.app
```

Tests use simulated storage and USB responses; they do not flash or reset hardware. Before marking a device family supported, manually validate a known-good package and loader on that device: enumerate, inspect geometry, review the plan, flash, verify, reboot and confirm the device boots. Exercise disconnect and failure recovery only on suitable test hardware.

Out of scope: backup/restore UI, standalone erase/GPT repair, concurrent devices, archive extraction and automatic updates. The original Tkinter implementation is retained in `resources/legacy_main.py` for reference. See `THIRD_PARTY_NOTICES.md` and `vendor/PATCHES.md` for component provenance.
