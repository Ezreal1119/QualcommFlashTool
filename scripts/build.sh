#!/bin/zsh
set -euo pipefail
cd "${0:A:h:h}"
if ! command -v uv >/dev/null 2>&1; then
  print 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi
export UV_PROJECT_ENVIRONMENT=.app-venv
export MACOSX_DEPLOYMENT_TARGET=13.0
uv sync --frozen --extra dev --python "${QFT_PYTHON:-3.11.13}" --managed-python
if ! .app-venv/bin/python - <<'PYTHON'
import hashlib, json, os
from pathlib import Path
manifest = json.loads(Path("resources/runtime-manifest.json").read_text())
adb = Path("resources/bin/adb")
ready = (
    adb.is_file() and os.access(adb, os.X_OK)
    and hashlib.sha256(adb.read_bytes()).hexdigest() == manifest["adb_sha256"]
    and Path("resources/ADB-NOTICE.txt").is_file()
    and Path("resources/lib/libusb-1.0.dylib").is_file()
)
raise SystemExit(0 if ready else 1)
PYTHON
then
  scripts/prepare_resources.sh
fi
.app-venv/bin/python scripts/collect_licenses.py
.app-venv/bin/python -m pytest -q
.app-venv/bin/pyinstaller --noconfirm --clean QualCommFlashTool.spec
.app-venv/bin/python scripts/verify_bundle.py dist/QualcommFlashTool.app
if [[ -n "${QFT_SIGN_IDENTITY:-}" ]]; then
  scripts/sign.sh "dist/QualcommFlashTool.app"
fi
.app-venv/bin/python -c 'import shutil; shutil.rmtree("build/dmg-root", ignore_errors=True)'
mkdir -p build/dmg-root
ditto dist/QualcommFlashTool.app build/dmg-root/QualcommFlashTool.app
[[ -L build/dmg-root/Applications ]] || ln -s /Applications build/dmg-root/Applications
hdiutil create -volname 'Qualcomm Flash Tool' -srcfolder build/dmg-root -ov -format UDZO dist/QualcommFlashTool-2.3.2-arm64.dmg
shasum -a 256 dist/QualcommFlashTool-2.3.2-arm64.dmg > dist/QualcommFlashTool-2.3.2-arm64.dmg.sha256
