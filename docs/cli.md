# Terminal flash

The terminal entry reuses the existing folder parser, immutable plan, worker protocol, ADB-to-EDL binding and checked flash engine. It does not open a window or require an updated desktop App/DMG.

## Start

From a source checkout, run `scripts/flash.sh`. The launcher uses uv and the pinned Python runtime without changing your current working directory. It prompts for an extracted firmware folder. Alternatively:

```sh
scripts/flash.sh "/Volumes/Firmware/DT50 package"
```

Paths with spaces or Chinese characters are supported. Interactive input also accepts a quoted or shell-escaped path dragged from Finder. ZIP input is rejected. `scripts/flash.sh --help` and `--version` do not access devices. With project dependencies installed, `qft-flash` or `.app-venv/bin/python -m qft.cli` are equivalent entries.

First use installs Python dependencies through uv. Missing project ADB/libusb are prepared automatically using `scripts/prepare_resources.sh`, with output saved to `cli-bootstrap.log` in the application data directory. This requires network access and Xcode command-line tools; the system ADB is unchanged.

## Confirm and execute

1. Offline folder checks find one unambiguous loader/XML set and validate the images. Missing/ambiguous files are errors; no rows are silently skipped.
2. A read-only scan requires exactly one available EDL or authorized wired ADB device. Missing, offline, unauthorized, multiple or unidentifiable devices fail before confirmation.
3. The review shows the full firmware/loader paths, device identity and USB port, storage hint, operation counts, XML list and layout notes. A local `plan.json` contains every file hash and sector range. Package checks do not establish model compatibility; confirm the package is intended for the target. Device storage is checked after confirmation.
4. Type exactly `FLASH` to authorize EDL transition, flashing, default verification and reboot. Anything else, including EOF, cancels. There is no `--yes` bypass.
5. The existing worker rechecks all fingerprints and current device identity before mode changes, binds ADB-to-EDL through the same USB path, checks actual capacities before writing, then runs programs, patches and verification. Required failures stop subsequent commands. There is no automatic retry, resume or reset after failure. Reboot follows verified success; unavailable verification prevents automatic reboot and returns a nonzero code.

Default output shows stages, current file and byte progress. Raw logs, JSON Lines events, requests, plans and results are saved under `~/Library/Application Support/QualcommFlashTool/tasks/` and appear in desktop History the next time it opens. Nothing is uploaded.

## Stop and exclusivity

Quit the desktop app before starting a CLI session. The terminal uses the desktop's existing application lock, so an already installed GUI needs no change. Concurrent terminal sessions are blocked too. `QFT_DATA_DIR` changes the data/lock directory for isolated tests; use the default directory for normal device work.

Ctrl+C, terminal hangup and SIGTERM request a cooperative stop. The worker runs in a separate session so terminal signals cannot interrupt its current USB write directly. Stopping takes effect at a protocol operation boundary; finishing a large current image can take time. Closing an output pipe also requests a safe stop.

The supervisor enforces a 30-second connection deadline, 75-second outer EDL wait deadline (the workflow waits 60 seconds), 120 seconds without structured activity, and 600 seconds without activity during offline preparation. Active writing has no fixed total duration. Stalled/crashed workers are terminated and reaped; an abnormal exit cannot be reported as success. Logs are retained for inspection. Automatic recovery is not attempted.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Flash and default verification succeeded; reboot was requested. |
| 1 | Input, runtime, protocol or required-operation failure. |
| 2 | Confirmation declined, input ended, or command-line usage error. |
| 3 | Verification incomplete, reboot failed, or completion lacks the required reboot confirmation. |
| 130 | Interrupted or timed out. |

The CLI has automated simulated-device/process coverage. It has not been used to flash hardware in this development session; the shared engine's earlier DT50 hardware run is recorded separately in [the hardware test record](hardware/DT50-2026-11-12.md).
