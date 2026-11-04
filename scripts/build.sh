#!/bin/zsh
set -euo pipefail
cd "${0:A:h:h}"
export UV_PROJECT_ENVIRONMENT=.app-venv
export MACOSX_DEPLOYMENT_TARGET=13.0
uv sync --frozen --extra dev --python "${QFT_PYTHON:-3.11.13}" --managed-python
if [[ ! -x resources/bin/adb || ! -f resources/lib/libusb-1.0.dylib ]]; then
  print 'Provide arm64 adb and libusb in resources/bin and resources/lib first. See README.' >&2
  exit 1
fi
.app-venv/bin/python scripts/collect_licenses.py
.app-venv/bin/python -m pytest -q
.app-venv/bin/pyinstaller --noconfirm --clean QualCommFlashTool.spec
.app-venv/bin/python scripts/verify_bundle.py dist/QualcommFlashTool.app
if [[ -n "${QFT_SIGN_IDENTITY:-}" ]]; then
  scripts/sign.sh "dist/QualcommFlashTool.app"
fi
mkdir -p build/dmg-root
ditto dist/QualcommFlashTool.app build/dmg-root/QualcommFlashTool.app
[[ -L build/dmg-root/Applications ]] || ln -s /Applications build/dmg-root/Applications
hdiutil create -volname 'Qualcomm Flash Tool' -srcfolder build/dmg-root -ov -format UDZO dist/QualcommFlashTool-2.0.0-arm64.dmg
shasum -a 256 dist/QualcommFlashTool-2.0.0-arm64.dmg > dist/QualcommFlashTool-2.0.0-arm64.dmg.sha256
