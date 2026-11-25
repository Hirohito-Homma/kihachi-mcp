#!/bin/bash
# JS 内包済み（Freeze）の KIHACHI Live Device.amxd を、選んだ Ableton User Library または Project へ導入する。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/maxforlive"
LIBRARY=""
ASSUME_YES=false
FILES=("KIHACHI Live Device.amxd")
LEGACY_JS_FILES=("kihachi.device.js" "kihachi.bridge.js")

while [ "$#" -gt 0 ]; do
  case "$1" in
    --library)
      [ "$#" -ge 2 ] || { echo "--library にはフォルダのパスが必要です" >&2; exit 2; }
      LIBRARY="$2"
      shift 2
      ;;
    --yes)
      ASSUME_YES=true
      shift
      ;;
    *)
      echo "不明な引数: $1" >&2
      exit 2
      ;;
  esac
done

for name in "${FILES[@]}"; do
  [ -f "$SOURCE/$name" ] || {
    echo "配布ファイルがありません: $SOURCE/$name" >&2
    exit 1
  }
done
if [ "$(head -c 4 "$SOURCE/KIHACHI Live Device.amxd")" != "ampf" ]; then
  echo "KIHACHI Live Device.amxd の形式を確認できません" >&2
  exit 1
fi
if command -v python3 >/dev/null 2>&1 && [ -f "$ROOT/scripts/build_amxd.py" ]; then
  python3 "$ROOT/scripts/build_amxd.py" --check --amxd "$SOURCE/KIHACHI Live Device.amxd"
fi

if [ -z "$LIBRARY" ]; then
  echo "Ableton Liveを終了してから、FinderでUser Library（またはProject）フォルダを選んでください。"
  LIBRARY="$(osascript -e 'POSIX path of (choose folder with prompt "Ableton LiveのUser LibraryまたはProjectフォルダを選択")')" || {
    echo "フォルダ選択を取り消しました"
    exit 1
  }
fi
[ -d "$LIBRARY" ] || { echo "導入先フォルダが見つかりません: $LIBRARY" >&2; exit 1; }
LIBRARY="$(cd "$LIBRARY" && pwd)"
DEST="$LIBRARY/Presets/Audio Effects/Max Audio Effect"

for name in "${FILES[@]}" "${LEGACY_JS_FILES[@]}"; do
  if [ -L "$DEST/$name" ] || { [ -e "$DEST/$name" ] && [ ! -f "$DEST/$name" ]; }; then
    echo "通常のファイルではないため導入を中止します: $DEST/$name" >&2
    exit 1
  fi
done

echo "KIHACHI Live Device の導入先:"
echo "  $DEST"
echo "導入ファイル: ${FILES[*]}（kihachi.device.js / kihachi.bridge.js 内包済み）"
echo "既存ファイルが異なる場合や旧外部 JS がある場合は、導入先内の .kihachi-backups に退避します。"
if [ "$ASSUME_YES" != true ]; then
  read -r -p "この場所へ導入しますか？ [y/N] " reply
  case "$reply" in y|Y|yes|YES) ;; *) echo "変更せず終了しました"; exit 0 ;; esac
fi

mkdir -p "$DEST"
BACKUP=""
backup_file() {
  local target="$1"
  local name="$2"
  if [ -z "$BACKUP" ]; then
    BACKUP="$LIBRARY/.kihachi-backups/$(date +%Y%m%d-%H%M%S)-$$"
    mkdir -p "$BACKUP"
  fi
  cp -p "$target" "$BACKUP/$name"
}

for name in "${FILES[@]}"; do
  if [ -e "$DEST/$name" ] && ! cmp -s "$SOURCE/$name" "$DEST/$name"; then
    backup_file "$DEST/$name" "$name"
  fi
done

# 旧バージョンで配置されていた外部 .js が残ると内包版より優先される可能性があるため退避・削除する
for name in "${LEGACY_JS_FILES[@]}"; do
  if [ -f "$DEST/$name" ]; then
    backup_file "$DEST/$name" "$name"
    rm -f "$DEST/$name"
    echo "  旧外部JS退避  $name"
  fi
done

for name in "${FILES[@]}"; do
  if [ -f "$DEST/$name" ] && cmp -s "$SOURCE/$name" "$DEST/$name"; then
    echo "  変更なし  $name"
    continue
  fi
  temp_file="$(mktemp "$DEST/.kihachi-install.XXXXXX")"
  cp -p "$SOURCE/$name" "$temp_file"
  mv -f "$temp_file" "$DEST/$name"
  echo "  導入完了  $name"
done

if [ -n "$BACKUP" ]; then
  echo "旧ファイルのバックアップ: $BACKUP"
fi
echo "Liveを開き、User Library（またはProject）の KIHACHI Live Device をSetに1つ置いてください。"
echo "Studioで Live: READY を確認したら利用できます。"
if [ -t 0 ] && [ "$ASSUME_YES" != true ]; then
  read -r -p "Enter キーで閉じます…" _
fi
