# ARCHITECTURE

KIHACHI MCP は FastMCP の薄い Tool 層と、dataclass の Domain 層、その間の Service 層で構成する。

## 流れ

```
MCP Client
    ↓ JSON
tools/          引数を受け取り、Service を呼び、JSON を返す
    ↓ dataclass
services/       生成・変換のルールを持つ
    ↓ dataclass
models/         SongSpec / TrackSpec / ProjectPlan
```

Tool は JSON だけを外に出す。計算は Service に置く。

## Python package structure

```
kihachi-mcp/
├── server.py                 # FastMCP エントリ。Tool を登録する
├── pyproject.toml
├── README.md
├── docs/
│   ├── ROADMAP.md
│   ├── ARCHITECTURE.md
│   └── API.md
├── models/                   # Domain models（dataclass）
│   ├── __init__.py
│   ├── songspec.py           # SongSpec
│   ├── track.py              # TrackSpec
│   └── project_plan.py       # ProjectPlan
├── services/                 # Application services
│   ├── __init__.py
│   ├── song_service.py       # SongService.generate_songspec
│   └── project_service.py    # ProjectService.create_project_from_songspec
├── tools/                    # MCP Tool adapters（JSON I/O）
│   ├── hello.py
│   ├── brain.py
│   └── project_builder.py
└── tests/
    ├── test_song_service.py
    └── test_project_service.py
```

`models` と `services` はパッケージ（`__init__.py` あり）。`tools` はモジュール群として `server.py` から直接 import する。実行時の import ルートはリポジトリ直下（`pythonpath = ["."]`）。

## レイヤー責務

| 層 | 役割 | 戻り値 |
| --- | --- | --- |
| `tools/` | FastMCP 公開 API。引数検証は型ヒントに任せる | JSON (`dict`) |
| `services/` | 小節数、デフォルトトラック、色と type の決定 | dataclass |
| `models/` | データの形と `to_dict()` / `from_dict()` | dataclass |

## 変換ルール

### SongSpec

- `bars = max(1, round(length_minutes * 32))`
- デフォルト tracks: Kick, Bass, Dub Chords, Lead, FX
- `mood` は入力として受け取るが、SongSpec には保存しない

### ProjectPlan

- `project_name` は `"Untitled"`
- SongSpec の genre / tempo / key / length_minutes / bars をコピー
- トラック展開:

| name | type | color |
| --- | --- | --- |
| Kick | MIDI | Red |
| Bass | MIDI | Blue |
| Dub Chords | MIDI | Purple |
| Lead | MIDI | Green |
| FX | Audio | Gray |

未知のトラック名は type=`MIDI`、color=`Gray`。

## 公開 API の境界

`server.py` が登録する Tool だけが互換対象。

```python
mcp.add_tool(hello)
mcp.add_tool(generate_songspec)
mcp.add_tool(create_project_from_songspec)
```

Service や dataclass のメソッド名は MCP クライアントから見えない。
