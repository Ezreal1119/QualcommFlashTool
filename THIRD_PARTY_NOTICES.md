# Third-party components

This is an internal-use build. No public redistribution or notarization is implied.

- **EDL / edlclient 3.62**, © B. Kerler and contributors, GPLv3. Source snapshot and full license are in `vendor/`. Upstream: https://github.com/bkerler/edl . `vendor/baseline.json` records original SHA-256 hashes and local source provenance. `vendor/PATCHES.md` documents changes. No proprietary Firehose loaders or firmware are included.
- **Qt / PySide6 6.8.3**, The Qt Company and contributors, LGPLv3/GPLv3/commercial licensing; deployed as dynamic frameworks. License texts are retained in the installed PySide6 package and copied by the build script. https://www.qt.io/licensing/
- **Android Debug Bridge**, Android Open Source Project. The complete SDK platform-tools notice is included in `resources/ADB-NOTICE.txt`.
- **libusb**, libusb contributors, LGPL-2.1-or-later. License included in `resources/LIBUSB-LICENSE.txt`.
- Python and additional Python dependencies retain their individual licenses in `resources/python-licenses/` in release builds. `uv.lock` pins the dependency resolution.

Before any external release, determine the application's licensing and provide corresponding source and all required notices. This internal build does not settle that decision.
