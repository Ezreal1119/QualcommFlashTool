#!/bin/zsh
set -euo pipefail
cd "${0:A:h:h}"
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  print 'Resource preparation requires a native Apple Silicon macOS terminal.' >&2
  exit 1
fi
if ! xcrun --find clang >/dev/null 2>&1; then
  print 'Install Xcode command-line tools first: xcode-select --install' >&2
  exit 1
fi
manifest_value() {
  python3 -c 'import json, sys; print(json.load(open("resources/runtime-manifest.json"))[sys.argv[1]])' "$1"
}
sha256() { shasum -a 256 "$1" | cut -d ' ' -f 1; }
download_checked() {
  local url="$1" destination="$2" expected="$3"
  curl --fail --location --retry 2 --connect-timeout 20 --max-time 180 "$url" -o "$destination"
  [[ "$(sha256 "$destination")" == "$expected" ]] || {
    print "Download checksum mismatch: $url" >&2
    exit 1
  }
}
work="$(mktemp -d "${TMPDIR:-/tmp}/qft-resources.XXXXXX")"
trap 'rm -rf "$work"' EXIT
adb_version="$(manifest_value adb_version)"
expected_adb="$(manifest_value adb_sha256)"
adb_directory=''
if [[ -n "${QFT_ADB_SOURCE:-}" ]]; then
  # Accept either a platform-tools directory or an adb executable/symlink.
  adb_directory="$(python3 -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]).expanduser().resolve(strict=True); print(p.parent if p.is_file() else p)' "$QFT_ADB_SOURCE")"
  if [[ ! -f "$adb_directory/adb" || ! -f "$adb_directory/NOTICE.txt" ]]; then
    print 'ADB override must contain adb and NOTICE.txt. Unset QFT_ADB_SOURCE to download the bundled version automatically.' >&2
    exit 1
  fi
  if [[ "$(sha256 "$adb_directory/adb")" != "$expected_adb" ]]; then
    print "ADB override does not match $adb_version. Unset QFT_ADB_SOURCE and run scripts/build.sh to download the correct version automatically." >&2
    exit 1
  fi
elif [[ -x resources/bin/adb && -f resources/ADB-NOTICE.txt && "$(sha256 resources/bin/adb)" == "$expected_adb" ]]; then
  print "Using the existing project ADB $adb_version."
else
  print "Downloading project ADB $adb_version from Google; the system ADB is unchanged."
  download_checked "$(manifest_value adb_archive_url)" "$work/platform-tools.zip" "$(manifest_value adb_archive_sha256)"
  unzip -q "$work/platform-tools.zip" -d "$work/sdk"
  adb_directory="$work/sdk/platform-tools"
  [[ -f "$adb_directory/NOTICE.txt" && "$(sha256 "$adb_directory/adb")" == "$expected_adb" ]] || {
    print 'Downloaded ADB does not match the controlled SDK revision.' >&2
    exit 1
  }
fi

print 'Building project libusb for macOS 13+…'
download_checked "$(manifest_value libusb_source)" "$work/libusb.tar.bz2" "$(manifest_value libusb_source_sha256)"
tar -xjf "$work/libusb.tar.bz2" -C "$work"
libusb_source="$work/libusb-$(manifest_value libusb_version)"
(cd "$libusb_source" && export MACOSX_DEPLOYMENT_TARGET=13.0; CFLAGS='-O2 -mmacosx-version-min=13.0' LDFLAGS='-mmacosx-version-min=13.0' ./configure --disable-static && make -j4)
install_name_tool -id @rpath/libusb-1.0.dylib "$libusb_source/libusb/.libs/libusb-1.0.0.dylib"
codesign --force --sign - "$libusb_source/libusb/.libs/libusb-1.0.0.dylib"
mkdir -p resources/bin resources/lib
if [[ -n "$adb_directory" ]]; then
  install -m 755 "$adb_directory/adb" resources/bin/adb
  cp "$adb_directory/NOTICE.txt" resources/ADB-NOTICE.txt
fi
# All downloads and compilation finish before replacing existing runtime files.
[[ ! -f resources/lib/libusb-1.0.dylib ]] || chmod u+w resources/lib/libusb-1.0.dylib
cp "$libusb_source/libusb/.libs/libusb-1.0.0.dylib" resources/lib/libusb-1.0.dylib
cp "$libusb_source/COPYING" resources/LIBUSB-LICENSE.txt
print 'ADB and libusb are ready in this project. The system ADB was not changed.'
