# API

KIHACHI MUSIC AI の公開 MCP Tool。戻り値は JSON（`hello` のみ文字列）。

確認:

```bash
uv run fastmcp list server.py
```

期待: Tools (3) — `hello` / `generate_songspec` / `create_project_from_songspec`

---

## hello

接続確認。

### 入力

なし

### 出力

```text
Hello from KIHACHI MCP
```

---

## generate_songspec

曲の仕様（SongSpec）を生成する。内部で `SongService.generate_songspec()` を呼び、dataclass を JSON にする。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| genre | str | ジャンル |
| tempo | int | BPM |
| key | str | キー（例: `D#m`） |
| length_minutes | float | 尺（分） |
| mood | str | ムード。仕様には保存されない |

### 出力

```json
{
  "genre": "dub techno",
  "tempo": 110,
  "key": "D#m",
  "length_minutes": 5,
  "bars": 160,
  "tracks": ["Kick", "Bass", "Dub Chords", "Lead", "FX"]
}
```

`bars` は `round(length_minutes * 32)`。5 分なら 160。

---

## create_project_from_songspec

SongSpec から ProjectPlan を作る。内部で `SongSpec.from_dict()` → `ProjectService.create_project_from_songspec()` → JSON。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| songspec | dict | `generate_songspec` と同じ JSON |

### 出力

```json
{
  "project_name": "Untitled",
  "genre": "dub techno",
  "tempo": 110,
  "key": "D#m",
  "length_minutes": 5,
  "bars": 160,
  "tracks": [
    {"name": "Kick", "type": "MIDI", "color": "Red"},
    {"name": "Bass", "type": "MIDI", "color": "Blue"},
    {"name": "Dub Chords", "type": "MIDI", "color": "Purple"},
    {"name": "Lead", "type": "MIDI", "color": "Green"},
    {"name": "FX", "type": "Audio", "color": "Gray"}
  ]
}
```

---

## 典型フロー

1. `hello` で接続を確認する
2. `generate_songspec` で SongSpec を得る
3. その JSON を `create_project_from_songspec` に渡す
