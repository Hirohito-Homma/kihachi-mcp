#!/bin/bash
# KIHACHI Live Device と2つのJSを、選んだAbleton User Libraryへ導入する。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/maxforlive"
LIBRARY=""
ASSUME_YES=false
FILES=("KIHACHI Live Device.amxd" "kihachi.device.js" "kihachi.bridge.js")

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

if [ -z "$LIBRARY" ]; then
  echo "Ableton Liveを終了してから、FinderでUser Libraryフォルダを選んでください。"
  LIBRARY="$(osascript -e 'POSIX path of (choose folder with prompt "Ableton LiveのUser Libraryフォルダを選択")')" || {
    echo "フォルダ選択を取り消しました"
    exit 1
  }
fi
[ -d "$LIBRARY" ] || { echo "User Libraryフォルダが見つかりません: $LIBRARY" >&2; exit 1; }
LIBRARY="$(cd "$LIBRARY" && pwd)"
DEST="$LIBRARY/Presets/Audio Effects/Max Audio Effect"

for name in "${FILES[@]}"; do
  if [ -L "$DEST/$name" ] || { [ -e "$DEST/$name" ] && [ ! -f "$DEST/$name" ]; }; then
    echo "通常のファイルではないため導入を中止します: $DEST/$name" >&2
    exit 1
  fi
done

echo "KIHACHI Live Device の導入先:"
echo "  $DEST"
echo "導入ファイル: ${FILES[*]}"
echo "既存ファイルが異なる場合は、User Library内の .kihachi-backups に退避します。"
if [ "$ASSUME_YES" != true ]; then
  read -r -p "この場所へ導入しますか？ [y/N] " reply
  case "$reply" in y|Y|yes|YES) ;; *) echo "変更せず終了しました"; exit 0 ;; esac
fi

mkdir -p "$DEST"
BACKUP=""
for name in "${FILES[@]}"; do
  if [ -e "$DEST/$name" ] && ! cmp -s "$SOURCE/$name" "$DEST/$name"; then
    if [ -z "$BACKUP" ]; then
      BACKUP="$LIBRARY/.kihachi-backups/$(date +%Y%m%d-%H%M%S)-$$"
      mkdir -p "$BACKUP"
    fi
    cp -p "$DEST/$name" "$BACKUP/$name"
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
echo "Liveを開き、User Libraryの KIHACHI Live Device をSetに1つ置いてください。"
echo "Studioで Live: READY を確認したら利用できます。"
if [ -t 0 ] && [ "$ASSUME_YES" != true ]; then
  read -r -p "Enter キーで閉じます…" _
fi
