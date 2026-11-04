#!/bin/zsh
set -euo pipefail
cd "${0:A:h:h}"
: "${QFT_ADB_SOURCE:?Set QFT_ADB_SOURCE to a platform-tools directory containing adb and NOTICE.txt}"
work="$(mktemp -d "${TMPDIR:-/tmp}/qft-libusb.XXXXXX")"
trap 'rm -rf "$work"' EXIT
curl --fail --location --max-time 120 https://github.com/libusb/libusb/releases/download/v1.0.29/libusb-1.0.29.tar.bz2 -o "$work/libusb.tar.bz2"
expected="$(python3 -c 'import json; print(json.load(open("resources/runtime-manifest.json"))["libusb_source_sha256"])')"
actual="$(shasum -a 256 "$work/libusb.tar.bz2" | cut -d ' ' -f 1)"
[[ "$actual" == "$expected" ]] || { print 'libusb source checksum mismatch' >&2; exit 1; }
tar -xjf "$work/libusb.tar.bz2" -C "$work"
(cd "$work/libusb-1.0.29" && export MACOSX_DEPLOYMENT_TARGET=13.0; CFLAGS='-O2 -mmacosx-version-min=13.0' LDFLAGS='-mmacosx-version-min=13.0' ./configure --disable-static && make -j4)
mkdir -p resources/bin resources/lib
expected_adb="$(python3 -c 'import json; print(json.load(open("resources/runtime-manifest.json"))["adb_sha256"])')"
actual_adb="$(shasum -a 256 "$QFT_ADB_SOURCE/adb" | cut -d ' ' -f 1)"
[[ "$actual_adb" == "$expected_adb" ]] || { print 'ADB binary does not match the pinned SDK revision' >&2; exit 1; }
cp "$QFT_ADB_SOURCE/adb" resources/bin/adb
cp "$QFT_ADB_SOURCE/NOTICE.txt" resources/ADB-NOTICE.txt
[[ ! -f resources/lib/libusb-1.0.dylib ]] || chmod u+w resources/lib/libusb-1.0.dylib
cp "$work/libusb-1.0.29/libusb/.libs/libusb-1.0.0.dylib" resources/lib/libusb-1.0.dylib
cp "$work/libusb-1.0.29/COPYING" resources/LIBUSB-LICENSE.txt
install_name_tool -id @rpath/libusb-1.0.dylib resources/lib/libusb-1.0.dylib
codesign --force --sign - resources/lib/libusb-1.0.dylib
