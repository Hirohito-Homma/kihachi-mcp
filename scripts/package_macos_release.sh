#!/bin/bash
# Package a built app with the Finder installer and its Live device assets.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo "使い方: $0 <KIHACHI MUSIC AI.app> <出力.zip>" >&2
  exit 2
fi
APP="$1"
OUTPUT="$2"
if [ ! -d "$APP" ]; then
  echo "アプリが見つかりません: $APP" >&2
  exit 1
fi
PARENT="$(cd "$(dirname "$APP")" && pwd)"
INSTALLER="$PARENT/scripts/Liveデバイスをインストール.command"
for REQUIRED in "$INSTALLER" "$PARENT/maxforlive/KIHACHI Live Device.amxd" "$PARENT/maxforlive/kihachi.device.js" "$PARENT/maxforlive/kihachi.bridge.js"; do
  if [ ! -f "$REQUIRED" ]; then
    echo "配布ファイルが見つかりません: $REQUIRED" >&2
    exit 1
  fi
done
if [ -e "$OUTPUT" ]; then
  echo "既存のZIPを上書きしません: $OUTPUT" >&2
  exit 1
fi
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/kihachi-release.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/$(basename "$APP")"
mkdir "$STAGE/scripts" "$STAGE/maxforlive"
ditto "$INSTALLER" "$STAGE/scripts/$(basename "$INSTALLER")"
for NAME in 'KIHACHI Live Device.amxd' kihachi.device.js kihachi.bridge.js; do
  ditto "$PARENT/maxforlive/$NAME" "$STAGE/maxforlive/$NAME"
done
ditto -c -k "$STAGE" "$OUTPUT"
echo "配布用: $OUTPUT"
