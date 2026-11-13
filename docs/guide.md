# Qualcomm Flash Tool — Detailed Guide

[Quick start](../README.md)

A compact PySide6 / QML desktop application for macOS 13+ on Apple Silicon. The default window is a 520 × 780 portrait workspace with a permanent console, five-stage progress and a small Configure button. The interface uses English throughout, including installations that previously saved a Chinese language preference. There are no Devices or Settings pages. This build is for internal use. DT50/UFS flashing and storage verification were exercised on physical hardware on 2026-11-12; the user confirmed successful Android startup on 2026-11-13. This does not establish compatibility with other devices. See [the hardware test record](hardware/DT50-2026-11-12.md).

## Run

Install [uv](https://docs.astral.sh/uv/) and use the pinned Python runtime:

```sh
uv python install 3.11.13
UV_PROJECT_ENVIRONMENT=.app-venv uv sync --frozen --extra dev --python 3.11.13 --managed-python
.app-venv/bin/python main.py
```

`--demo` opens a disconnected visual preview; device commands are disabled. `--screenshot /absolute/path.png` captures a window and exits. `QT_QPA_PLATFORM=offscreen` enables headless previews. Startup loads the bundled runtime and performs a read-only ADB/USB scan. No-device, unauthorized and offline states are displayed immediately. Startup never changes device mode or writes firmware. Selecting or dropping a folder first opens a full-path confirmation. Only Confirm & check starts the offline and device-check pipeline; it never automatically starts flashing.

## Use

1. Choose or drop an **extracted firmware folder**. ZIP packages are not accepted. Confirm the full path before scanning starts. Checks never start flashing automatically.
2. Use the device selector and refresh button directly in the main window. The app discovers the loader, pairs XML, infers a storage hint and validates images automatically. On EDL devices, inspection uploads the loader to RAM and reads storage geometry without writing firmware.
3. Click **Flash…**, review the exact target and firmware, and confirm **Flash & reboot**. An authorized USB ADB target enters EDL automatically; the worker waits for the same physical USB port and requires the ADB connection to disappear before binding EDL. An inspected EDL target starts directly. Identity ambiguity, unsupported transitions and failed checks prevent writing.
4. Progress follows **Check → Connect → Flash → Verify → Reboot**. Writing shows actual byte progress; other stages use an activity indicator. The console stays visible and contains timestamped stages, USB connection steps, raw output and the final verification results. Scrolling or selecting log text pauses following; the down-arrow returns to the latest output.
5. Open **Configure… → Files** to replace the loader, add or deselect rawprogram/patch XML, override storage type or open the package in Finder to replace images. XML **Edit** validates syntax, detects external changes, creates a backup and saves atomically. Close does not revert file selections; use **Check changes** to validate them again.
6. **Configure… → Plan** lists write ranges, patch locations and layout warnings. The flash confirmation also has a **Plan…** button. The appearance control is kept at the bottom of Files; the interface language is English and there is no separate settings page. Device/file choices stay frozen during execution.
7. **Cancel check** cancels file/device checks; **Stop safely** requests a stop at the next protocol operation boundary. Failed or interrupted tasks do not reset or retry writes. Reboot is automatic only after all required operations and default verification pass; incomplete verification retains a separate, confirmed manual reboot action. This flow always requests reboot after verified success, including when an older installation saved a disabled reboot preference.
8. Open **History** in the console toolbar for task records and diagnostic ZIP export. Records and logs remain local; nothing is uploaded. Long paths, file lists, confirmation dialogs and XML content scroll within the compact window.

The app does not establish device-model / firmware compatibility. Vendor authorization requirements remain in effect. Permission problems are reported without silently elevating privileges or changing sudoers.

## Supported package behavior

- UFS / eMMC; standard `<data><program .../></data>` and `<data><patch .../></data>` XML; raw and Android sparse images. Empty program filenames describe unprogrammed regions and generate no writes.
- Image paths resolve relative to their XML file and must remain inside the package directory. Loaders and manually selected XML may be in different directories; absolute paths are retained.
- Sector sizes 512 / 4096, file-sector offsets, bounded writes, final-sector zero padding, numeric LUN ordering and original operation order. All programs execute before patches. Mixed sector sizes, missing images and unsupported XML features are rejected. The standard `partofsingleimage` boolean is accepted as layout metadata; file offsets remain explicit.
- Sparse RAW / FILL / DONT_CARE / CRC chunks are validated; DONT_CARE ranges are expanded to zero. Sparse checksum verification occurs before device connection.
- XML `num_partition_sectors` is retained as the allocation limit. Actual transfers use the smaller of that nonzero limit and the image's available sector count after its file offset; a zero count derives the transfer size from the image. Only the final sector is zero-padded. The app never expands a short image to fill the partition or turns a zero count into a whole-partition erase. Both the XML allocation and actual writes are checked against device capacity. The UI shows layout notes and actual transfer sizes. These semantics were checked against [QDL's raw-image transfer implementation](https://github.com/linux-msm/qdl/blob/master/src/firehose.c).
- Patch XML may use either `<patches>` or `<data>` roots. Non-DISK file patches are retained in the plan and must have equivalent ordered DISK patches for the same programmed image. Symbolic affine arithmetic translates file-relative sectors and CRC32 ranges into disk addresses for comparison. Missing, mismatched, reordered, out-of-range or unprovable mirrors are rejected. Only the validated DISK operations are sent; source GPT binaries and XML are never modified by flashing.
- XML arithmetic permits integer constants, NUM_DISK_SECTORS, +, -, *, exact division and CRC32(sector, byte_length) for patch values. There is no arbitrary expression execution.
- Capacity must be reported by the device's per-LUN getstorageinfo response. Unknown or inconsistent geometry blocks flashing. No capacity is guessed from a firmware image or an old partition table.
- Initial and final write acknowledgements, patch acknowledgements, final primary/backup GPT header and entry CRCs, plus final boot / vbmeta SHA-256 where advertised by the loader. Unavailable checks are explicitly listed. Overlapping writes and patches are included in expected digest reconstruction.
- Firehose startup messages are drained to a bounded idle boundary, including the complete advertised function list. An acknowledged NOP rechecks capabilities when reconnecting to an existing programmer. USB reads have bounded timeouts and retain bytes beyond short Sahara reads; incomplete or failed raw writes are never retried.
- After an acknowledged reset, the host continues receiving reset logs and waits up to 15 seconds for the selected EDL device to disappear. An ACK without the expected USB transition is reported as a reboot failure. USB disappearance alone does not establish that Android completed startup.
- A stop request takes effect between operations. Stalled workers are killed by the supervisor. No automatic resumption or rollback is claimed.

## Architecture

`qft/package.py` builds immutable, fingerprinted execution plans. `qft/engine.py` controls tasks independently of the UI; `qft/workflow.py` owns the explicitly confirmed ADB-to-EDL transition and hands the bound target to the same engine in one worker session. `qft/backend.py` uses pinned EDL primitives through a checked adapter; upstream qfil and its exit-code heuristics are not used. The QML controller talks to a separate worker over versioned JSON Lines. No UI thread performs USB transactions.

Folder discovery is capped at 20,000 entries, six subdirectory levels and 15 seconds; hidden directories and symlinks are skipped. Entire mount roots and the home directory are rejected. Files are rehashed before every device session. Protocol errors, missing ACKs, short transfers, disconnects and process crashes stop the task. There is no raw-write retry. The worker never implicitly resets a device on error.

Settings, task records, full logs and inspection reports are stored in `~/Library/Application Support/QualcommFlashTool`. A single-instance lock avoids competing task supervisors. Startup marks unfinished records as interrupted. Diagnostic exports include the last 20 task records, paths and device identifiers; no firmware images or credentials are included and nothing is uploaded. `QFT_DATA_DIR` can override this location for testing.

## Build an internal installer

Provide an official Android SDK `platform-tools` directory with ADB 36.0.0-13206524. The resource preparation script builds libusb 1.0.29 from its checksum-pinned release source for macOS 13; it requires Xcode command-line tools:

```sh
QFT_ADB_SOURCE=/path/to/platform-tools scripts/prepare_resources.sh
scripts/build.sh
```

Build outputs: `dist/QualcommFlashTool.app`, `dist/QualcommFlashTool-2.3.2-arm64.dmg`, SHA-256 sidecar. The app includes Python, Qt, ADB, libusb and the EDL snapshot; users do not need Python or Homebrew. Resource binaries and generated build directories are not tracked. Dependency versions are locked by `uv.lock`; the EDL baseline has a separate SHA-256 manifest.

Internal builds use local ad-hoc signing, not Apple notarization. They may require explicit user approval in macOS before opening. A future external release requires a licensing decision and Developer ID distribution credentials. `QFT_SIGN_IDENTITY` and optional `QFT_NOTARY_PROFILE` enable the signing/notarization script. No credentials are stored in this repository.

## Verification

```sh
.app-venv/bin/python -m pytest -q
.app-venv/bin/python scripts/verify_bundle.py dist/QualcommFlashTool.app
```

Tests use simulated storage and USB responses; they do not flash or reset hardware. Before marking a device family supported, manually validate a known-good package and loader on that device: enumerate, inspect geometry, review the plan, flash, verify, reboot and confirm the device boots. Exercise disconnect and failure recovery only on suitable test hardware.

Out of scope: backup/restore UI, standalone erase/GPT repair, concurrent devices, archive import and automatic updates. The original Tkinter implementation is retained in `resources/legacy_main.py` for reference. See `THIRD_PARTY_NOTICES.md` and `vendor/PATCHES.md` for component provenance.
