# Studio × Jarvis Repository Audit

監査日: 2026-10-03\
対象: `/Users/user/MusicAI/kihachi-mcp`（ブランチ `feature/studio-integration`、origin より 2 commit 先行、作業ツリーに未コミット変更あり）\
方法: 実コードの入口・登録・呼び出しを読んだ。README や過去ドキュメントだけでは「実装済み」にしていない。\
`ai-company-os/` は対象外。Live への write / Save / Export は行っていない。

状態の意味:

- 実装済み: 呼び出し経路までコードで確認した
- 部分実装: 型・画面・関数はあるが、本仕様の責務を満たさない
- 未実装: 該当コードがない
- UNKNOWN: この監査で実行・接続を確認していない

---

## Existing Architecture

確認したプロセス境界は次のとおり。

```
Human
  → KIHACHI Studio
       HTTP 127.0.0.1:8765
       src/kihachi_mcp/studio/__main__.py
       src/kihachi_mcp/studio/app.py  (stdlib ThreadingHTTPServer)
       src/kihachi_mcp/studio/static/index.html
  → StudioRuntime / StudioDialogue / voice_io
       src/kihachi_mcp/services/studio_runtime.py
  → 二つの Live 経路（別実装）
       A. Max for Live UDP 127.0.0.1:17771
          services/live_bridge.py
          services/live_execution_service.py
          services/live_approval_gate.py
       B. AbletonGPT Remote Script TCP 127.0.0.1:9877
          gateway/client.py
          services/abletongpt_kits.py
          sibling repo /Users/user/MusicAI/abletongpt
             ableton_remote_script/AbletonGPT/__init__.py
             （既定ポート 9877 をコードで確認）
```

MCP サーバーは Studio とは別プロセス。

- 入口: `src/kihachi_mcp/server.py` の `FastMCP("KIHACHI MUSIC AI")`
- 起動: `server.main()` → `gateway/serve.py` の `serve_stdio()`
- HTTP MCP: `kihachi serve --transport streamable-http`（`gateway/serve.py`）
- CLI: `src/kihachi_mcp/cli.py`。Live を触るコマンドは、返信ポートを一つに保つため起動中の Studio HTTP を呼ぶ（`cli.py` 先頭コメントと `studio_client.studio_post`）

Studio は MCP サーバーを経由して Ableton を書いていない。Studio は `StudioRuntime` を直接呼ぶ。

関連リポジトリ（ディレクトリ実在を確認）:

| パス | 確認した関係 |
|---|---|
| `/Users/user/MusicAI/kihachi-mcp` | 今回の中心。Studio・MCP・in-process Brain |
| `/Users/user/MusicAI/kihachi-music-ai` | 別パッケージ `kihachi-music-ai`。`pyproject.toml` の説明は Music Brain / SongSpec / MIDI / ACE-Step。`kihachi-mcp` の Python は `kihachi_music_ai` を import していない。ジャンル JSON は移植コメントのみ（`knowledge/genre_database.py`） |
| `/Users/user/MusicAI/kihachi-music-ai-wt-ollama` | `kihachi-music-ai` の git worktree。ブランチ `feature/youtube-ops-external-audio-ollama` |
| `/Users/user/MusicAI/abletongpt` | 別パッケージ。Remote Script が TCP 9877 で待ち受ける。`kihachi-mcp` はソケットクライアントとして呼ぶ |
| `/Users/user/MusicAI/_archive/abletongpt-live-extension` | アーカイブディレクトリ。今回の実行経路には入っていない |

フレームワーク:

- Studio UI: 単一 HTML + 素の JS。React / Next ではない
- Studio API: Python 標準ライブラリの HTTP
- MCP: FastMCP
- テスト: pytest（`tests/`）、一部 Node（`tests/js/`）

---

## Studio Existing Features

入口: `python -m kihachi_mcp.studio`（`studio/__main__.py`）。ポート 8765、ホスト 127.0.0.1。

`index.html` にある画面（見出しを確認）:

- KIHACHIと対話（テキスト、録音、読み上げ、提案を制作指示へ反映）
- 制作指示（brief から候補生成）
- パート単位 MIDI Composer
- AIが解釈した内容
- MIDI候補（簡易試聴。注記どおり Live 送信と承認はしない）
- プロジェクト（保存候補、SongSpec、構成、トラック）
- AIレビュー
- 修正案（採用するまで候補は変わらない）
- Liveへ適用（画面文言: 承認 → ドライラン → 適用 → 読み戻し検証）
- アレンジメントに展開
- エフェクト、つまみ再設定、MIX、サイドチェイン、マスタリング、音圧
- サンプル投入
- SYSTEM DIAGNOSTICS / AI / Ableton / Project
- ABLETON PLAN（ドライラン）
- ABLETON VERIFICATION

スタイルに `JARVIS HUD theme` という CSS コメントがある。Jarvis の会話・状態機械・Intent は無い。

対話（`services/studio_dialogue.py`）:

- 実装済み: 規則ベースの短命セッション。Live / 生成 / 削除は対話から実行しない、と返答する
- 部分実装: 自然言語を構造化 Intent にしない。サンプル検索と brief 文面の提案まで

音声（`services/voice_io.py`）:

- 実装済み: macOS の `say`、ローカル `OnDeviceASR.app`、用語補正、一時ファイル削除
- 部分実装: 認識テキストは対話ターンへ渡る。Jarvis Intent パイプラインは無い
- プロバイダはコード上、クラウドフォールバック無し（`cloud_fallback: False`）

プロジェクト表示（`services/production_workspace.py` の `project_view`）:

- 実装済み: 開いている MIDI 候補から tempo / key / sections / tracks / note counts
- 未実装: 本仕様の Production State（adopted takes、verified、unknown を一つの状態として持つモデル）

---

## MCP Existing Features

登録箇所: `server.py` の `mcp.add_tool` と `gateway/tools.py` → `register_live_tools` → `register_sample_tools` / `register_sound_tools`。

### 既存 stdio ツール（`server.py`）

`create_song`, `list_projects`, `get_project`, `review_song`, `revise_song`, `approve_song`, `dry_run_ableton_plan`, `execute_ableton_plan`, `apply_ableton_effects`, `apply_ableton_mix`, `apply_ableton_master`, `apply_ableton_retune`, `apply_ableton_sidechain`, `verify_ableton_project`, `measure_loudness`, `ollama_status`, `doctor`, `preview_local_ai_song`, `compose_midi_part`, `hello`, `generate_songspec`, `create_project_from_songspec`, `create_ableton_plan`, `create_midi_plan`, `prepare_ableton_handoff`, `inspect_live_state`, `live_device_catalogue`, `discover_live_capabilities`, `create_live_mutation_plan`, `request_live_execution`, `execute_live_request`, `verify_live_execution`, `expand_session_to_arrangement`, `review_songspec`, `remember_song`, `search_memory`, `orchestrate_song`

Live 系 MCP ツールの多くは Studio HTTP を呼ぶ（`tools/studio.py`）。Studio が止まっていると実行できない。

### Gateway ツール（AbletonGPT ソケット）

`gateway/catalog.py` の `EXPOSED` で status が `SUPPORTED` の名前は、対応する `mcp.tool` 登録がある。

読み取り: `ableton_ping`, `ableton_status`, `server_status`, `ableton_tool_catalogue`, `ableton_list_tracks`, `ableton_list_devices`, `ableton_list_instruments`, `ableton_search_browser`, `ableton_get_arrangement`, `get_midi_clip_notes`, `list_drum_pads`, `get_arrangement_midi_notes`, `ableton_get_song_time`, `ableton_get_playback_state`, `sample_library_status`, `search_samples`, `get_sample`, `get_sample_metadata`, `find_samples_for_song`, `sample_loading_capabilities`

書き込み（`confirm=true` が無いと送らない。`ApprovalGate` は使わない）: `set_tempo`, `ableton_play`, `ableton_stop`, `ableton_create_midi_track`, `ableton_create_audio_track`, `ableton_create_midi_clip`, `ableton_add_notes`, `ableton_set_volume`, `ableton_set_pan`, `ableton_mute`, `ableton_solo`, `ableton_load_stock_instrument`, `ableton_device_probe`, `load_sample_to_simpler`, `load_sample_to_pad`, `add_arrangement_midi_notes`, `replace_arrangement_midi_notes`, `duplicate_arrangement_clip`, `ableton_set_song_time`, `ableton_set_start_time`, `ableton_jump_song_time`, `produce_ableton_song`, `index_sample_library`, `select_sample`, `place_sample`, `probe_indexed_sample_audio`, `place_audio_one_shot_sample`

破壊的注釈（`destructiveHint: true`、やはり `confirm` フラグ）: `delete_arrangement_midi_notes`, `delete_arrangement_clip`, `move_arrangement_clip`

カタログ上 `PARTIAL` または `NOT IMPLEMENTED` で、MCP ツールとしては未登録: `launch_clip`, `stop_all_clips`, `set_device_parameter`, `load_preset`, `add_native_device`, `toggle_cue_at_playhead`, `rename_track`, `delete_track`, `delete_clip`, `overwrite_project`, `replace_device`, `remove_device`, `save_over_existing_project`, `back_to_arrangement`, `continue_playing`, `play_selection`

`sound.py` に `launch_clip_once` / `audition_clip` 関数はある。`register_sound_tools` はそれらを登録していない。

### MCP resources

`mcp.resource` / `add_resource` の登録は `src/` に無い。ジャンル YAML/JSON はパッケージデータであり、MCP resource ではない。

---

## Brain Existing Features

`kihachi-mcp` 内の Brain はプロセス内ファサード。

- `src/kihachi_mcp/api/brain.py` の `Brain`
- 依存: `SongService`, `ProjectService`, `ReviewService`, `MemoryService`, `GenerationService`, `Orchestrator`
- MCP: `generate_songspec`, `review_songspec`, `create_project_from_songspec`, `orchestrate_song`, `remember_song`, `search_memory`, `compose_midi_part`

`services/orchestrator.py` の `Orchestrator` は、SongSpec 生成 → レビュー → メモリ → ProjectPlan の順次実行。Ableton も自然言語 Intent も扱わない。

Studio の音楽生成は別経路。

- `studio_interpreter.interpret_with_fallback` → `ProductionBrief` → `midi_candidate_builder`
- レビューは `production_review.review_candidate`
- 修正は `production_revision.revise_candidate`（人が `accept` するまで採用しない）

`kihachi-music-ai` の `models.py` / `reviewer.py` / `midi_review.py` は別パッケージとして存在する。`kihachi-mcp` からの import は確認できなかった。中身の関数一覧は本監査では追っていない。統合状態は **未接続**。

---

## Ollama Existing Features

重複クライアントは作らないこと。既存は次の二つで、下位は同じ `OllamaClient`。

| 箇所 | 役割 |
|---|---|
| `services/studio_interpreter.py` `OllamaClient` | `127.0.0.1:11434` の `POST /api/chat`。timeout 既定 180 秒。応答 256KiB 超は失敗。キャンセルあり |
| `services/ai_provider.py` `OllamaProvider` | 同じ client を包む。`generate` / `generate_structured` |
| `services/ollama_status.py` `probe_ollama` | 生存とインストール済みモデル。ダウンロードしない |

確認した振る舞い:

- 構造化出力: `format: STUDIO_SCHEMA`（brief 解釈）。`OllamaProvider.generate_structured` は任意スキーマを渡せる
- JSON: `parse_model_json`（フェンス除去）のあと `validate_model_intent`（許可キー、型、範囲、小節は 4 の倍数）
- 再試行: `interpret_with_fallback` は最大 3 回（既定 2）。失敗後は `interpret_brief_offline`（明示値と既定値）。キャンセルは再試行しない
- ループバック以外の URL は `split_ollama_url` が拒否する
- オフライン時の brief は `provider: deterministic` になる。成功とは書いていない

別プロバイダ（Jarvis から新規に使わない）:

- `DeterministicProvider`
- `OpenAIProvider`（`gpt-6.1-sol` / `gpt-6-astra`、`OPENAI_API_KEY`）。`ai_cost_control.MonthlyCostGuard` がある

Ollama は brief → ProductionBrief 用。本仕様の制作 Intent（`evaluate_arrangement` など）用のプロンプトは無い。

---

## Approval Existing Features

三系統ある。一つに見えない。

### 1. 候補の人間承認（Studio / MCP）

`StudioRuntime.approve` は候補 ID を `_approved_ids` と `.approved` ファイルに記録する。画面ボタンは「この候補を承認」。未承認の候補は `send_to_ableton` が拒否する。修正採用後は新しい候補になり、再承認が必要（画面文言）。

これは「この Take を Live に送ってよいか」であり、操作ごとの Diff 承認ではない。

### 2. Live mutation の ApprovalGate

`services/live_approval_gate.py`:

- 計画ハッシュにトークンを結ぶ。計画が変わるとトークンは無効
- TTL 900 秒
- 実行試行後に idempotency key を消費（失敗も含む）
- 保存先は `KIHACHI_LIVE_APPROVAL_PATH`。未設定ならメモリのみ

`StudioRuntime` は送信処理の中で `self._gate.approve(plan)` を自分で呼んでいる（例: 838, 993, 1334 行付近）。人がゲート画面でトークンを発行する流れではない。人の操作は候補承認と `confirmed=true`。

ゲートの状態名は `models/live_contract.py`: `ready`, `approval_required`, `blocked`, `executing`, `partially_applied`, `verification_failed`, `verified`, `unavailable`。Jarvis の `IDLE` / `UNDERSTANDING` / `HUMAN_AUDITION_REQUIRED` は無い。

### 3. Gateway の `confirm=true`

`gateway/` は `ApprovalGate` を import しない。呼び出し側が `confirm=true` を付けると書き込む。Studio の候補承認とは独立。

本仕様の「Pending action を APPROVE / REJECT する Approval Center」は **未実装**。

---

## Verification Existing Features

実行と検証は、Live 経路では分離されている。

### Max for Live 経路

- `models/live_receipt.py`: `verified` は全操作の readback が期待と一致したときだけ。不一致は `verification_failed`
- `studio_workflow.readback_verification`: tempo / tracks / clips / notes / arrangement を PASS / FAIL / SKIP。不一致なら `VERIFICATION FAILED`。コメントどおり、音楽的な良し悪しは含まない
- `StudioRuntime.verify`: Live から読めなければ `EXTERNAL VERIFICATION REQUIRED`。成功にはしない
- `send_to_ableton`: receipt が `verified` のときだけ `verify()` を足す

### Gateway 経路

- `gateway/song_execute.py`: 読み戻しが取れなければ `verified` を成功にしない分岐がある
- `gateway/transport.py`: 書き込み後に観測し、一致しなければ `unconfirmed`
- `gateway/samples/load.py`: メーターで `verified_by_meter` または `pending`

UNKNOWN を SUCCESS にしない、という方針は Live receipt と Studio verify にはある。Jarvis 状態 `UNKNOWN` は無い。

音楽的成功とコマンド成功の分離は、verify の文言では守られている。自然言語の「ベースが多いか」を検証する比較器は無い。

---

## Ableton Integration

| 経路 | コード | 人が承認する単位 | この監査での実機 |
|---|---|---|---|
| Max for Live UDP 17771 | `live_bridge.py`, `maxforlive/kihachi.device.js` | 候補承認 + confirmed。実行は StudioRuntime | 未実施。LIVE VERIFICATION PENDING |
| AbletonGPT TCP 9877 | `gateway/client.py`, `abletongpt_kits.py`, sibling Remote Script | `confirm=true` のみ | 未実施。LIVE VERIFICATION PENDING |
| キット読み込み | `AbletonGPTKitLoader`。Studio が Live 計画のあと空ドラムへ 1 回 | Studio の適用フロー内 | 未実施 |

`diagnostics.py` は sibling `../abletongpt` を探す。接続成否はこの監査では測っていない。

Save / Export 用の MCP ツールはカタログ上 `save_over_existing_project` が `NOT IMPLEMENTED`。Studio の音圧測定は、人が書き出した WAV のパスを読む（`measure_loudness`）。

---

## Existing Agent/Orchestration Code

検索語 `jarvis` のヒットは `index.html` の CSS コメント 1 件だけ。

既存で近いもの:

| 名前 | 実体 | Jarvis との差 |
|---|---|---|
| `services/orchestrator.py` | SongSpec パイプライン | 制作対話の司令塔ではない |
| `tools/orchestrator.py` `orchestrate_song` | 上記の MCP ラッパ | 同左 |
| `StudioDialogue` | 規則対話 | Intent / Plan / 状態機械が無い |
| `studio_interpreter` | brief → JSON | 曲全体の制作指示用。範囲指定の編集 Intent ではない |
| `candidate_live_planner.py` | 候補 → Live 操作列 | 自然言語から計画しない |
| `live_mutation_planner.py` | Live 変更計画 | 承認トークンモデル用。Jarvis Plan 型ではない |
| `gateway/song_plan.py` / `song_execute.py` | 曲生成計画と実行 | Gateway 専用。Studio 対話と未接続 |

`ai-company-os` に別の agent / approval がある。対象外。Studio へ接続されていない（この監査の範囲では import を見ていない。ディレクトリが別ツリーであることは確認した）。

---

## Reusable Components

Jarvis から新規に作らず、次を拡張する。

- Studio の単一 HTML と `StudioApp` ルート。新しい Studio を立てない
- `StudioDialogue.turn` を、実行しない Intent 抽出の入口にする
- `OllamaClient` / `interpret_with_fallback` / `validate_model_intent` / `parse_model_json`
- `project_view` と `StudioRuntime` の候補ストア（`candidate_store_dir`）
- `readback_verification` と `LiveExecutionReceipt`
- `ApprovalGate`（トークン、ハッシュ、TTL、再実行禁止）
- Gateway の読み取りツール（トラック、デバイス、アレンジ、ソングタイム、再生状態）
- `inspect_live_state`（Max for Live 側のスナップショット）
- `voice_io` はテキストと同じターンへ渡す現在の形を維持する
- `MemoryService` は SongSpec 記憶として残す。チャット履歴の代替にはしない

---

## Missing Components

未実装（コード無し）だった項目のその後:

- Jarvis の状態、Intent、Plan、承認、読み戻し順、Brain への音楽判断、音声テキストの同一入口は実装済み
- 壊れたモデル JSON は Intent にせず UNKNOWN
- 二つの Live 読み取りでテンポかトラック数が違えば UNKNOWN。成功にしない
- パート計画の承認と拒否は Studio にあり、既存の `ApprovalGate` に記録する
- 保留中の操作（採用待ちの修正案、承認済みで未送信のパート計画）は、Jarvis の「承認」欄に一覧で出る。修正案はそこで採用・却下できる。新しい承認ストアは作っていない
- Brain の指摘と提案は Jarvis 計画の `proposal` になる。書き込み案は空
- 「読み戻しを確認」は Studio の中の偽の Set で、再承認のあとだけ応答を返す。実機は読まない
- パート計画の実機送信は、人が押す「実機の Live に送る」だけ。Save と Export は未接続
- Session MIDI クリップの読み取り・事実評価・改訂プレビュー（`services/jarvis/clip_review.py`、`JarvisOrchestrator.inspect_midi_clip` / `ask`、Studio `/api/jarvis/turn`・`/api/jarvis/clip-preview`）は実装済み。読み取りには `get_midi_clip_notes` のみを使い、`complete=false`・切り詰め（4096ノート超など）・形式不正・非MIDI・対象曖昧時は `UNKNOWN` で止め、Live への書き戻しは行わない（LIVE VERIFICATION PENDING）
- Session / Project / Long-term の三層メモリは作っていない

---

## Duplicate Risks

新しく作ると二重になるもの:

- 二つ目の Studio、二つ目の MCP サーバー、二つ目の Ollama client
- `ApprovalGate` と並ぶ別トークン庫。足りないのは操作単位の人間 UI と、Gateway `confirm=true` との接続
- `Orchestrator` を Jarvis と別名で再実装すること。曲生成パイプラインは残し、制作司令は別モジュールにする
- `kihachi-music-ai` の SongSpec を、mcp 内 `SongService` と無関係に再実装すること
- Ableton 書き込みを Studio と Gateway の両方に足すこと。書き込みの正は、既存どおり Max for Live は StudioRuntime、ソケット操作は Gateway。Jarvis はどちらも直接ソケットを開かない
- `ai-company-os` の approval / agent を Studio に持ち込むこと

既存の二重（消す前に境界を決める）:

- 候補承認（人）と `ApprovalGate.approve`（Runtime が自分で記録）と Gateway `confirm=true`（人の画面が無い）
- Live 読み取りが Max for Live スナップショットと AbletonGPT `get_state` の二つ。値の対応は未照合（UNKNOWN）

---

## UNKNOWN

- 今のマシンで Ollama、Live、AbletonGPT が応答するか（この監査はプロセスを叩いていない）
- `kihachi-music-ai` のどの関数が mcp 内 Brain と意味的に重複するか（import が無いことだけ確認）
- Gateway の `confirm=true` を、過去の運用で人が毎回見ていたか（コード上はフラグ）
- 二つの Live 読み取りが同じ Set で同じテンポ・トラック数を返すか
- On-device ASR がこの Mac にインストール済みか（`voice_status` は未実行）
- `launch_clip_once` の呼び出し元がテスト以外にあるか（MCP 未登録であることだけ確認）

---

## Recommended Integration Boundary

Jarvis は Studio プロセスの中のオーケストレータにする。新しい製品プロセスにしない。

```
Studio UI
  → Jarvis（新規。StudioRuntime の隣）
       Intent（新規スキーマ）
       Plan（新規スキーマ）
       状態（新規。Live receipt 状態とは別）
  → 読み取り
       候補: StudioRuntime / project_view
       Live（Studio 経路）: 既存 inspector
       Live（ソケット）: 既存 gateway 読み取り関数をライブラリとして呼ぶ
  → 音楽判断が要るとき
       既存 Brain / ProductionBrief / production_review
       kihachi-music-ai パッケージは、import 境界を決めるまで呼ばない
  → 書き込み
       未承認なら失敗
       Max for Live 変更: 既存 StudioRuntime（候補承認 + ApprovalGate）
       ソケット変更: 既存 gateway 関数。ただし confirm フラグだけでは通さない
  → 検証
       既存 readback_verification / LiveExecutionReceipt
       読めなければ UNKNOWN。verified にしない
```

Phase 1 は読み取りだけ。Live write、Save、Export は人の明示指示まで行わない。

LIVE VERIFICATION PENDING
