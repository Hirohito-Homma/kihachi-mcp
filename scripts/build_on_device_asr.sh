#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET=${KIHACHI_VOICE_APP_DIR:-"$HOME/Library/Application Support/KIHACHI/OnDeviceASR.app"}
mkdir -p "$TARGET/Contents/MacOS"
cp "$ROOT/src/kihachi_mcp/studio/voice/Info.plist" "$TARGET/Contents/Info.plist"
swiftc "$ROOT/src/kihachi_mcp/studio/voice/OnDeviceASR.swift" \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist \
  -Xlinker "$ROOT/src/kihachi_mcp/studio/voice/Info.plist" \
  -o "$TARGET/Contents/MacOS/OnDeviceASR"
chmod 755 "$TARGET/Contents/MacOS/OnDeviceASR"
codesign --force --sign - "$TARGET"
printf 'Built %s\n' "$TARGET"
