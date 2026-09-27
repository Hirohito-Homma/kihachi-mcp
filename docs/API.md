# API

KIHACHI MUSIC AI の公開 MCP Tool。戻り値は JSON（`hello` のみ文字列）。

確認:

```bash
uv run fastmcp list server.py
```

期待: Tools (17) — `hello` / `generate_songspec` / `create_project_from_songspec` / `create_ableton_plan` / `create_midi_plan` / `prepare_ableton_handoff` / `inspect_live_state` / `live_device_catalogue` / `create_live_mutation_plan` / `request_live_execution` / `execute_live_request` / `verify_live_execution` / `expand_session_to_arrangement` / `review_songspec` / `remember_song` / `search_memory` / `orchestrate_song`

### 全Live系Toolに共通する前提

- **Google Lyria は廃止済み**です。`generate_audio` は存在しません。
- **Live への変更には人間承認が必要**です。承認トークンは Tool の戻り値に含まれません。
- **既存内容を自動上書きしません。** 利用者所有の要素は `conflicts` で返します。
- **自動再実行しません。**
- **読戻し成功まで完了扱いしません。** `verified` 以外は完了ではありません。
- **CIでは実機接続しません。**

Live 系 Tool はすべて例外を投げず、`status` と `error` を含む JSON を返します。秘密情報はエラーメッセージにも含めません。

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

曲の仕様（SongSpec）を生成する。Brain → GenerationService → KnowledgeService → SongService。省略したテンポとキー、およびトラックはジャンル知識から補完する。未知のジャンルはエラー。公開JSON形は従来どおり。知識の出典（id / version / source）は Python API `Brain.generate_from_knowledge()` が返す。

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

## inspect_live_state

Ableton Live の接続確認と状態読取り。**何も変更しない。** Live 自動操作の入口。

### 入力

なし

### 出力

```json
{
  "schema_version": 1,
  "health": {
    "connected": true,
    "live_version": "12.1.0",
    "device_version": "kihachi-live-device/0.1.0",
    "protocol_version": 1,
    "protocol_supported": true,
    "error_code": "",
    "error": ""
  },
  "snapshot": { "...": "LiveStateSnapshot" }
}
```

`snapshot` は Transport 未設定・未接続・プロトコル不一致・Live バージョン非対応のとき `null` になり、`health` に理由が入る。

`snapshot` の主なキー: `schema_version` / `live_version` / `set_name` / `set_path` / `set_fingerprint` / `tempo` / `time_signature` / `is_playing` / `is_recording` / `tracks` / `scenes` / `session_clips` / `arrangement_clips` / `devices` / `observed_at`。詳細は [MAXFORLIVE.md](MAXFORLIVE.md)。

`set_fingerprint` は構造から再計算する。`is_playing`、`is_recording`、`observed_at` は含まない。

---

## live_device_catalogue

KIHACHI がロードを許可する Live 標準デバイス一覧を返す。Live へは接続しない。

### 出力

```json
{
  "schema_version": 1,
  "devices": [{"name": "Drum Rack", "category": "instrument", "role": "drums"}],
  "external_plugins_supported": false,
  "note": "Availability differs by Live edition and version. ..."
}
```

対応は Drum Rack / Simpler / Operator / Wavetable / Drift / Auto Filter / EQ Eight / Compressor / Saturator / Echo / Hybrid Reverb の11種。

**これは候補リストであり利用可能性の保証ではない。** 判定根拠は `inspect_live_state` の `snapshot.devices` のみ。存在しないデバイスは `blocked` になり、別デバイスへ自動置換しない。外部プラグイン（VST3 / AU / CLAP）は対象外。

---

## create_live_mutation_plan

ProjectPlan から Session View の変更計画を作る。**不活性で、何も実行しない。**

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| project_plan | dict | ProjectPlan JSON（`include_arrangement=true` の出力） |
| live_state | dict \| null | 省略時は Live を読む。渡すとその snapshot に対して計画する |
| clip_bars | int | Session Clip の長さ（小節）。既定4 |

### 出力

`LiveMutationPlan` JSON。

| キー | 説明 |
| --- | --- |
| `schema_version` | 1 |
| `request_id` | この計画の識別子 |
| `idempotency_key` | 二重実行防止キー |
| `source_plan_hash` | 元 ProjectPlan のハッシュ |
| `set_fingerprint` | 計画時の Set 構造ハッシュ |
| `operations` | 操作列。各操作は `operation_id` / `op` / `target` / `arguments` / `preconditions` / `destructive` / `expected_readback` |
| `conflicts` | 実行を止める理由。1件でもあれば `status` は `blocked` |
| `warnings` | 人間が読むべき注意 |
| `destructive_operation_count` | 既存を変更する操作数 |
| `approval_required` | 承認が必要か |
| `expires_at` | 期限（既定15分） |
| `plan_hash` | 計画の同一性。変わると承認は無効 |
| `status` | `ready` / `approval_required` / `blocked` / `unavailable` |

主な `conflicts.kind`: `live_is_recording` / `live_is_playing` / `user_owned_clip` / `device_unavailable` / `no_tracks` / `no_sections`。

---

## request_live_execution

承認待ちの Live 実行要求を作る。**何も実行しない。**

承認トークンは所有者のみ読めるファイル（`0600`）へ書き出し、**戻り値には含めない。** 人間がそのファイルを開いてトークンを読み、`execute_live_request` へ渡す。この経路により、Tool 出力だけを持つアシスタントは自分の計画を承認できない。

### 入力

`create_live_mutation_plan` と同じ（`project_plan` / `live_state` / `clip_bars`）。

### 出力

```json
{
  "schema_version": 1,
  "status": "approval_required",
  "approval_required": true,
  "plan": { "...": "LiveMutationPlan" },
  "operation_count": 14,
  "destructive_operation_count": 1,
  "warnings": ["Set tempo changes from 120.0 to 110 BPM"],
  "approval_token_path": "/Users/me/Library/Application Support/KIHACHI/pending-approval.json",
  "approval_instructions": "A human must open approval_token_path, ..."
}
```

計画が `blocked` または `unavailable` のときは `approval_required` が `false` になり、`approval_token_path` は返らない。

`KIHACHI_LIVE_APPROVAL_TOKEN_PATH` で書き出し先を変更できる。

---

## execute_live_request

承認済み要求を**1回だけ**実行し、receipt を返す。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| request | dict | `request_live_execution` の出力、または `LiveMutationPlan` |
| approved | bool | 既定 false。false なら `approval_required` |
| approval_token | str | 承認ファイルから読んだトークン |

### 実行前の拒否条件

この順で検査し、1つでも該当すれば Set を変更しない。

1. `conflicts` がある → `blocked`
2. `operations` が空、または512件超 → `blocked`
3. `approved` が false → `approval_required`
4. トークンが不明・期限切れ → `approval_required` / `blocked`
5. `plan_hash` が承認時と違う → `blocked`（計画改変）
6. `idempotency_key` が使用済み → `blocked`（二重実行）
7. Live 不達 → `unavailable`
8. `set_fingerprint` が計画時と違う → `blocked`
9. 録音中 → `blocked`
10. 再生中に構造変更を含む → `blocked`

### 出力

`LiveExecutionReceipt` JSON。

| キー | 説明 |
| --- | --- |
| `request_id` | 対応する計画 |
| `status` | 下表 |
| `attempted_operations` | 送った `operation_id` |
| `completed_operations` | Live が適用を確認した `operation_id` |
| `failed_operation` | 止まった `operation_id` |
| `readback` | 操作ごとの `observed` と `matched` |
| `mismatches` | `operation_id` / `field_name` / `expected` / `observed` |
| `set_fingerprint_before` | 実行前 |
| `set_fingerprint_after` | 実行後 |
| `error` | 理由 |

| `status` | 意味 |
| --- | --- |
| `ready` | 操作なし |
| `approval_required` | 承認が必要。Set は変更なし |
| `blocked` | 拒否。Set は変更なし |
| `executing` | 実行中（中間状態） |
| `partially_applied` | **一部適用。残りは実行しない。自動再試行しない** |
| `verification_failed` | 全操作適用したが読戻し不一致 |
| `verified` | 全操作適用、かつ全操作読戻し一致 |
| `unavailable` | Live へ到達できない。Set は変更なし |

`verified` 以外は完了ではない。`partially_applied` と `verification_failed` は人間の確認が必要（[RECOVERY.md](RECOVERY.md)）。

実行を試行した時点で `idempotency_key` は使用済みになる。失敗しても同じ要求は再実行できない。部分適用の上に同じ計画を重ねると二重生成になるため。

---

## verify_live_execution

Live を読み直して receipt の主張を再確認する。**何も実行しない。**

Live は一部プロパティを非同期に更新するため、直後の receipt を後から独立に確認するための2回目の読戻し。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| request | dict | 対応する要求または計画 |
| receipt | dict | `execute_live_request` の出力 |

### 出力

`LiveExecutionReceipt` JSON。`verified` / `verification_failed` / `unavailable` のいずれか。`verified` と `verification_failed` 以外の receipt を渡した場合はそのまま返す。

---

## expand_session_to_arrangement

検証済み Session View パターンを Arrangement View へ展開する計画を作る。**不活性で、何も実行しない。**

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| project_plan | dict | ProjectPlan JSON |
| session_receipt | dict | Session 実行の receipt |
| live_state | dict \| null | 省略時は Live を読む |
| clip_bars | int | 既定4 |

### 拒否条件

- `session_receipt.status` が `verified` でない → `blocked`（`session_not_verified`）
- 録音中・再生中 → `blocked`
- 対象トラックまたはシーンが Set にない → `blocked`（`missing_track` / `missing_session_clip`）
- 対象小節範囲に既存 Arrangement Clip がある → `blocked`（`arrangement_range_occupied`）
- 同一要求内でセクションが重なる → `blocked`

既存 Arrangement Clip を移動・トリム・削除しない。空き領域のみへ配置する。

### 小節から Live 時間への変換

```text
start_beats  = (start_bar - 1) * beats_per_bar
length_beats = length_bars * beats_per_bar
beats_per_bar = numerator * 4 / denominator
```

決定論的で、Set の拍子を使う。4/4 を仮定しない。3/4 の Set では17小節目は beat 48 になる。

### 出力

`LiveMutationPlan` JSON。`create_locator` と `place_arrangement_clip` を含む。実行は `request_live_execution` → `execute_live_request` と同じ承認経路を通る。

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

SongSpec と任意の ReviewResult をMemoryへ保存する。`KIHACHI_MEMORY_PATH`未設定時はプロセス内のみ、設定時はJSONファイルへ原子的に保存する。

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
| genre | str \| null | 大文字小文字を無視した部分一致。省略可 |
| approved_only | bool | Review合格だけに絞る。既定false |
| min_score | float | Reviewスコアの下限。既定0 |
| limit | int \| null | 最大件数。省略時は全件 |
| query | str \| null | ジャンル・キー・トラック・コメントの横断検索 |
| sort_by | str | `recent`（既定）または`score` |


---

## orchestrate_song

生成・Review・Memory保存・ProjectPlan作成を固定順序で実行する統合Tool。外部APIやAbleton操作は行わない。

### 入力

`generate_songspec`と同じ`genre`、`tempo`、`key`、`length_minutes`、`mood`を受け取る。`stop_on_review_failure`をtrueにすると、Review不合格時はMemory保存後にProject作成を止める。

### 出力

`songspec`、`review`、`memory`、`project`を含むJSONを返す。`project`はArrangementを含む。


---

## prepare_ableton_handoff

AbletonProjectPlanをLiveへ渡す前に静的検証する。Live接続・Set変更・ファイル書き込みは行わない。トラック名の一意性、テンポ範囲、Locatorの小節範囲と重なりを検査する。

### 入力

| 名前 | 型 | 説明 |
| --- | --- | --- |
| project_plan | dict | ProjectPlan JSON |

### 出力

`ready`、`errors`、`warnings`、`plan`を含むJSON。

これは Live 状態を見ない静的検証であり、`create_live_mutation_plan` の代わりにはならない。既存 Set との衝突検査は `create_live_mutation_plan` が行う。

---

## save_live_set

**未実装。** 設計のみ [issues/ISSUE-0021.md](issues/ISSUE-0021.md) に文書化している。

保存は取り消せない唯一の操作であり、Session と Arrangement の変更とは別の承認段階を必要とするため、この段階では実装しない。KIHACHI は Set を保存しない。保存は Live 側で人間が行う。

---

## Live 実行の典型フロー

```text
1. inspect_live_state              Live を読む。変更なし
2. create_live_mutation_plan       計画を作る。変更なし
3. request_live_execution          トークンをファイルへ書く。変更なし
   → 人間がファイルを開いてトークンを読む
4. execute_live_request            1回だけ実行。readback で検証
5. verify_live_execution           Live を読み直して再確認
6. expand_session_to_arrangement   verified のときだけ展開計画
7. request_live_execution          展開の承認
8. execute_live_request            展開を実行
9. verify_live_execution           再確認
```

各段階で `status` を確認してから次へ進む。`verified` 以外で先へ進まない。
