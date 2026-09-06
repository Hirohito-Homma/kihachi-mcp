# ARCHITECTURE

KIHACHI MCP は FastMCP の薄い Tool 層と、dataclass の Domain 層、その間の Service 層で構成する。

実装は src-layout（[ADR-0001](adr/0001-use-src-layout.md)）。コードは `src/kihachi_mcp/` に置き、リポジトリ直下は互換エントリとメタデータだけにする。

## 流れ

```
MCP Client
    ↓ JSON
kihachi_mcp.tools       引数を受け取り、Service を呼び、JSON を返す
    ↓ dataclass
kihachi_mcp.services    生成・変換のルールを持つ
    ↓ dataclass
kihachi_mcp.models      SongSpec / TrackSpec / ProjectPlan / Arrangement / ReviewResult
```

Tool は JSON だけを外に出す。計算は Service に置く。

## Python package structure

```
kihachi-mcp/
├── server.py                      # 互換エントリ。kihachi_mcp.server.main を呼ぶ
├── pyproject.toml
├── README.md
├── docs/
│   ├── ROADMAP.md
│   ├── ARCHITECTURE.md
│   ├── API.md
│   └── adr/
│       └── 0001-use-src-layout.md
├── src/kihachi_mcp/
│   ├── server.py                  # FastMCP 登録
│   ├── models/
│   ├── services/
│   ├── tools/
│   └── shared/
└── tests/
```

実行時の import ルートは `src/`。テストは `kihachi_mcp.*` だけを参照する。

## レイヤー責務

| 層 | 役割 | 戻り値 |
| --- | --- | --- |
| `kihachi_mcp.tools` | FastMCP 公開 API。Service を呼び JSON を返す | JSON (`dict`) |
| `kihachi_mcp.services` | 業務ロジック | dataclass |
| `kihachi_mcp.models` | データの形と `to_dict()` / `from_dict()` | dataclass |

### Services

| Service | 責務 |
| --- | --- |
| `SongService` | SongSpec の生成と検証 |
| `ProjectService` | ProjectPlan の生成とメタデータ準備 |
| `ReviewService` | ReviewResult の生成とコメント集約 |

公開 MCP Tool は増やさない。ReviewService は内部利用のみ。

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

`kihachi_mcp.server` が登録する Tool だけが互換対象。

```python
mcp.add_tool(hello)
mcp.add_tool(generate_songspec)
mcp.add_tool(create_project_from_songspec)
```

ルートの `server.py` は `kihachi_mcp.server.main` を呼ぶ互換エントリ。
