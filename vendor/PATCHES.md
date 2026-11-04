# Controlled EDL baseline

The original source is the installed `edlclient 3.62` snapshot. SHA-256 hashes before changes are recorded in `baseline.json`. The nearby local checkout reports commit 502db9c439b0d748ca38b86e02765290cae9873e; it is provenance only, not a claim that installed files match that checkout.

Changes in `Library/Connection/usblib.py`:
- Resolve the app-bundled libusb using QFT_LIBUSB, falling back to system discovery for development.
- Filter by an explicit USB bus/address before opening the device.

The application does not invoke upstream `qfil`, `firehose_client`, `configure`, `getlunsize`, shared edl_config.json caching or XML arithmetic. `qft/backend.py` supplies checked XML transfers, bounded payload negotiation, per-LUN geometry queries, direct non-retrying raw writes and explicit final ACK handling. `qft/expressions.py` implements the limited arithmetic language without eval. A failed Sahara handshake does not call cmd_reset. Worker stdout is reserved for versioned JSON Lines; upstream output goes to the per-task raw log.

Upstream code is retained for auditability. Unused CLI handlers and their expression handling are not application entry points.
