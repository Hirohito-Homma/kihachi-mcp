## KIHACHI Live Bridge プロトコル

KIHACHI MCP と Max for Live デバイス間のメッセージ契約です。`protocol_version = 1`。

前提として次を必ず守ります。

- **Google Lyria は廃止済み**です。音声生成プロバイダは存在しません。
- **Live への変更には人間承認が必要**です。プロトコルは承認を代行しません。
- **既存内容を自動上書きしません。** 利用者所有の要素は `conflict` として返します。
- **自動再実行しません。** 失敗は失敗として報告します。
- **読戻し成功まで完了扱いしません。** `verified` は読戻し一致のみを意味します。
- **CIでは実機接続しません。** 契約一致のみを検証します。

### トランスポート

- loopback UDP、`127.0.0.1` のみ
- 1リクエスト = 1データグラム、JSON（UTF-8）
- リクエスト上限 60,000 バイト、レスポンス上限 262,144 バイト
- 既定タイムアウト 5 秒
- 1計画あたりの操作数上限 512
- 同一 `request_id` の再送は拒否（送信側・受信側の両方で）

### リクエスト

```json
{
  "protocol": "kihachi.live",
  "version": 1,
  "request_id": "apply-000001",
  "token": "<session token>",
  "method": "ping | get_state | apply_operation",
  "payload": {}
}
```

`token` はトランスポート層が付加します。上位層（planner、executor、MCP tool）はトークンを一切扱いません。

### レスポンス

成功時。

```json
{
  "protocol": "kihachi.live",
  "version": 1,
  "request_id": "apply-000001",
  "ok": true,
  "result": {}
}
```

失敗時。

```json
{
  "protocol": "kihachi.live",
  "version": 1,
  "request_id": "apply-000001",
  "ok": false,
  "error": { "code": "operation_failed", "message": "session slot (0, 0) already holds a clip" }
}
```

エラーコード: `unavailable` / `timeout` / `disconnected` / `protocol` / `unauthorized` / `too_large` / `duplicate_request` / `operation_failed`。

`unauthorized` のメッセージには受信したトークン値を含めません。

### method: ping

制作操作を一切行わない接続確認です。

`result`:

| キー | 型 |
| --- | --- |
| `live_version` | string |
| `protocol_version` | int |
| `device_version` | string |

### method: get_state

`result` は `LiveStateSnapshot` 互換の JSON です。

| キー | 型 | 備考 |
| --- | --- | --- |
| `schema_version` | int | 1 以外は Python 側で拒否 |
| `live_version` | string | メジャー11未満は拒否 |
| `set_name` | string | |
| `set_path` | string | Python 側でプラットフォーム正規化 |
| `tempo` | float | |
| `time_signature` | object | `numerator` / `denominator` |
| `is_playing` | bool | |
| `is_recording` | bool | |
| `tracks` | array | `index` / `name` / `track_type` / `color` / `is_armed` / `is_frozen` / `device_names` |
| `scenes` | array | `index` / `name` / `color` |
| `session_clips` | array | `track_index` / `scene_index` / `name` / `length_beats` / `note_count` / `is_midi` / `looping` |
| `arrangement_clips` | array | `track_index` / `name` / `start_beats` / `length_beats` / `note_count` |
| `devices` | array | ロード可能な Live 標準デバイス。`name` / `available` / `category` |
| `observed_at` | string | ISO 8601 |

`set_fingerprint` はペイロードに含まれていても信頼しません。Python 側が構造から再計算します。対象は `set_path` / `set_name` / `tempo` / `time_signature` / `tracks` / `scenes` / `session_clips` / `arrangement_clips` で、`is_playing`、`is_recording`、`observed_at`、`live_version`、`devices` は除外します。再生ボタンを押しただけで承認済み計画が無効化されないようにするためです。

### method: apply_operation

`payload.operation` は 1 件の `LiveMutationOperation` です。

```json
{
  "operation_id": "004-create_session_clip",
  "op": "create_session_clip",
  "target": { "track_index": 0, "scene_index": 0 },
  "arguments": { "name": "Intro [K:1a2b3c4d]", "length_beats": 16.0, "looping": true },
  "preconditions": [
    { "kind": "not_recording", "arguments": {} },
    { "kind": "track_name_at_index", "arguments": { "track_index": 0, "name": "Kick [KIHACHI]" } },
    { "kind": "session_slot_empty", "arguments": { "track_index": 0, "scene_index": 0 } }
  ],
  "destructive": false,
  "expected_readback": { "track_index": 0, "scene_index": 0, "name": "Intro [K:1a2b3c4d]", "length_beats": 16.0, "looping": true }
}
```

`result`:

```json
{ "operation_id": "...", "op": "...", "observed": {} }
```

`observed` は `expected_readback` の全キーを含まなければなりません。欠落または不一致は `verification_failed` になります。

### 操作一覧と readback

| `op` | `target` | 主な `arguments` | `observed` |
| --- | --- | --- | --- |
| `set_tempo` | `scope` | `tempo` | `tempo` |
| `create_midi_track` | `track_index` | `name` / `color` | `track_index` / `name` / `track_type` |
| `create_audio_track` | `track_index` | `name` / `color` | `track_index` / `name` / `track_type` |
| `set_track_name` | `track_index` | `name` | `track_index` / `name` |
| `set_track_color` | `track_index` | `color` | `track_index` / `color` |
| `create_scene` | `scene_index` | `name` | `scene_index` / `name` |
| `create_session_clip` | `track_index` / `scene_index` | `name` / `length_beats` / `looping` | 同左 |
| `replace_clip_notes` | `track_index` / `scene_index` | `notes` | `track_index` / `scene_index` / `note_count` |
| `load_live_device` | `track_index` | `device_name` | `track_index` / `device_name` / `device_index` |
| `create_locator` | `scope` | `name` / `beats` | `name` / `beats` |
| `place_arrangement_clip` | `track_index` / `scene_index` | `name` / `start_beats` / `length_beats` | `track_index` / `name` / `start_beats` / `length_beats` |

`notes` の各要素は `pitch` / `start` / `duration` / `velocity` です。`start` と `duration` は Live の beat 単位（4分音符 = 1 beat）で、クリップ先頭からの相対値です。

### precondition 一覧

デバイス側は計画を信用せず、適用直前に再検査します。1件でも満たされなければ操作を適用せず `operation_failed` を返します。

| `kind` | `arguments` | 意味 |
| --- | --- | --- |
| `not_recording` | なし | 録音中でないこと |
| `transport_stopped` | なし | 再生中でないこと |
| `track_name_at_index` | `track_index` / `name` | 予測したインデックスに予測した名前のトラックがあること |
| `track_missing` | `name` | その名前のトラックが存在しないこと |
| `scene_name_at_index` | `scene_index` / `name` | 予測した位置に予測した名前のシーンがあること |
| `session_slot_empty` | `track_index` / `scene_index` | Clip Slot が空であること |
| `clip_is_managed` | `track_index` / `scene_index` | 既存 Clip が KIHACHI 所有であること |
| `device_available` | `device_name` | そのデバイスがロード可能であること |
| `arrangement_range_free` | `track_index` / `start_beats` / `length_beats` | Arrangement の対象範囲が空であること |

`track_name_at_index` が重要な理由は、トラック作成でインデックスがずれるためです。計画は作成順からインデックスを予測し、デバイスは名前で照合します。予測が外れた場合は推測せずに失敗します。

### 所有権

Live のトラックとシーンには任意メタデータを持たせられないため、名前を所有権の根拠にします。

- トラック・シーン: 末尾に ` [KIHACHI]`
- クリップ: ` [K:<8桁hash>]`

マーカーがない要素は**利用者所有**として扱い、読み取り以外を一切行いません。同名であることは所有の根拠になりません。利用者の `Kick` がある Set では、KIHACHI は `Kick [KIHACHI]` を別途作成します。

### バージョン非互換時

`protocol_version` が一致しない場合、`inspect_live_state` は `health.protocol_supported = false` を返し、計画も実行も行いません。デバイスを更新するか、サーバーを合わせてください。
