#!/bin/bash
# A Developer ID-signed .app and a preconfigured notarytool Keychain profile.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo "使い方: $0 <KIHACHI MUSIC AI.app> <notarytoolのKeychainプロファイル名>" >&2
  exit 2
fi
APP="$1"
PROFILE="$2"
if [ ! -d "$APP" ]; then
  echo "アプリが見つかりません: $APP" >&2
  exit 1
fi
if ! codesign -dv --verbose=4 "$APP" 2>&1 | grep -q 'Authority=Developer ID Application:'; then
  echo "Developer ID Applicationで署名されたアプリが必要です。" >&2
  exit 1
fi
codesign --verify --deep --strict "$APP"
PARENT="$(cd "$(dirname "$APP")" && pwd)"
BASE="$(basename "$APP" .app)"
SUBMISSION="$PARENT/$BASE-for-notary.zip"
FINAL="$PARENT/$BASE-notarized.zip"
if [ -e "$SUBMISSION" ] || [ -e "$FINAL" ]; then
  echo "既存のZIPを上書きしません: $SUBMISSION / $FINAL" >&2
  exit 1
fi
ditto -c -k --keepParent "$APP" "$SUBMISSION"
xcrun notarytool submit "$SUBMISSION" --keychain-profile "$PROFILE" --wait
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
ditto -c -k --keepParent "$APP" "$FINAL"
echo "配布用: $FINAL"
