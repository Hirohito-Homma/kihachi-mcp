"""Securely store a YouTube Data API key in KIHACHI's local API config."""

from __future__ import annotations

import json
import os
import sys
from getpass import getpass
from pathlib import Path
from tempfile import NamedTemporaryFile

from kihachi_mcp.services.live_paths import bridge_state_dir


def main() -> int:
    key = getpass("YouTube Data API key (入力は表示されません): ").strip()
    if len(key) < 20 or any(char.isspace() for char in key):
        print("キー形式を確認してください。値は表示・保存していません。", file=sys.stderr)
        return 2

    directory = Path(bridge_state_dir()).expanduser()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    target = directory / "reference-api.json"
    try:
        if target.exists() and target.stat().st_size > 16_384:
            raise ValueError
        settings = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
        if not isinstance(settings, dict):
            raise ValueError
    except (OSError, ValueError, json.JSONDecodeError):
        print("既存のAPI設定を読み取れません。既存設定は変更していません。", file=sys.stderr)
        return 1

    settings["YOUTUBE_DATA_API_KEY"] = key
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=".reference-api-", delete=False
        ) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            json.dump(settings, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        print("設定を安全に保存できませんでした。キーは画面へ出していません。", file=sys.stderr)
        return 1

    print(f"YouTube Data APIキーをローカル設定へ保存しました: {target}")
    print("Studioを再起動するとYouTube検索が利用できます。キー値は表示していません。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
