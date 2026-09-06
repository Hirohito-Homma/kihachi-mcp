# API

KIHACHI MUSIC AI の公開 MCP Tool。戻り値は JSON（`hello` のみ文字列）。

確認:

```bash
uv run fastmcp list server.py
```

期待: Tools (8) — `hello` / `generate_songspec` / `create_project_from_songspec` / `create_ableton_plan` / `generate_audio` / `review_songspec` / `remember_song` / `search_memory`

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

曲の仕様（SongSpec）を生成する。Brain → Knowledge Engine → SongService。省略したテンポとキー、およびトラックはジャンル YAML から補完する。未知のジャンルはエラー。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| genre | str | ジャンル |
| tempo | int \| null | BPM。省略、null、0以下ならジャンル既定値 |
| key | str \| null | キー（例: `D#m`）。省略または空文字ならジャンル既定値 |
| length_minutes | float | 尺（分）。省略時は5分 |
| mood | str \| null | 任意のムード。仕様には保存されない |

### 出力

```json
{
  "genre": "dub techno",
  "tempo": 110,
  "key": "D#m",
  "length_minutes": 5,
  "bars": 160,
  "tracks": ["Kick", "Bass", "Dub Chords", "Pad", "FX"]
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
| include_arrangement | bool | 省略時は false。true なら Arrangement 配列を追加 |

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
    {"name": "Pad", "type": "MIDI", "color": "Gray"},
    {"name": "FX", "type": "Audio", "color": "Gray"}
  ]
}
```

既定の出力形は従来どおり。`include_arrangement=true` の場合だけ、`arrangement` に name / start_bar / length_bars を持つセクション配列を追加する。


---

## 典型フロー

1. `hello` で接続を確認する
2. `generate_songspec` で SongSpec を得る
3. その JSON を `create_project_from_songspec` に渡す

---

## create_ableton_plan

ProjectPlanをAbleton向けの非実行プランへ変換する。
Live接続、ファイル書き込み、MIDI生成は行わない。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| project_plan | dict | ProjectPlan JSON。Arrangementを含める場合はオプトイン出力を渡す |

### 出力

```json
{
  "set_name": "Untitled",
  "tempo": 110,
  "tracks": [{"name": "Kick", "track_type": "MIDI", "color": "Red"}],
  "locators": [{"name": "Intro", "start_bar": 1, "length_bars": 32}]
}
```

---

## generate_audio

ProjectPlanの明示したトラックをGoogle Lyriaへ渡し、検証済み音声artifact receiptを返す。GEMINI_API_KEY未設定時はblockedを返す。Ableton操作は行わない。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| project_plan | dict | ProjectPlan JSON |
| target_track | str | 生成対象トラック名 |
| output_path | str | 保存先パス |
| prompt | str | 任意の生成指示 |
| negative_prompt | str | 任意の除外指示 |

### 出力

status、task_id、artifact_path、sha256等を持つAudioRenderResult JSON。APIキーは入力・出力に含めない。

### 設定

- `GEMINI_API_KEY`: 必須。Google AI APIキー。
- `LYRIA_MODEL`: 任意。既定値は `lyria-3.5`。
- `LYRIA_BASE_URL`: 任意。既定値はGoogle Gemini Interactions API。

---

## review_songspec

SongSpec を ReviewService で検証し、ReviewResult を JSON で返す。外部APIやAbleton操作は行わない。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| songspec | dict | `generate_songspec` の出力JSON |

### 出力

```json
{
  "approved": true,
  "score": 1.0,
  "comments": []
}
```


---

## remember_song

SongSpec と任意の ReviewResult をMCPプロセス内のMemoryへ保存する。外部DBやファイルは使わず、サーバー再起動で消える。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| songspec | dict | SongSpec JSON |
| review | dict \| null | 任意のReviewResult JSON |

## search_memory

Memoryへ保存した曲をジャンルで検索する。ジャンルを省略すると全件を返す。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| genre | str \| null | 大文字小文字を無視した完全一致。省略可 |
