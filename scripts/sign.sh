#!/bin/zsh
set -euo pipefail
: "${QFT_SIGN_IDENTITY:?Set QFT_SIGN_IDENTITY to a Developer ID Application identity}"
app="${1:?Pass the app path}"
# Nested libraries/frameworks are signed individually before the app envelope.
find "$app/Contents" -type f -print0 | while IFS= read -r -d '' path; do
  if file -b "$path" | /usr/bin/grep -q 'Mach-O'; then
    codesign --force --timestamp --options runtime --sign "$QFT_SIGN_IDENTITY" "$path"
  fi
done
find "$app/Contents" -type d -name '*.framework' -depth -print0 | while IFS= read -r -d '' path; do
  codesign --force --timestamp --options runtime --sign "$QFT_SIGN_IDENTITY" "$path"
done
codesign --force --timestamp --options runtime --entitlements "${0:A:h}/entitlements.plist" --sign "$QFT_SIGN_IDENTITY" "$app"
codesign --verify --deep --strict --verbose=2 "$app"
if [[ -n "${QFT_NOTARY_PROFILE:-}" ]]; then
  archive="${app:r}-notary.zip"
  ditto -c -k --keepParent "$app" "$archive"
  xcrun notarytool submit "$archive" --keychain-profile "$QFT_NOTARY_PROFILE" --wait
  xcrun stapler staple "$app"
fi
