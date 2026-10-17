# Studio × Jarvis Implementation Plan

根拠: `docs/STUDIO_JARVIS_AUDIT.md`（2026-10-03）。監査で見ていない事実は書いていない。\
分類: EXISTS / EXTEND / CREATE / REMOVE DUPLICATE / UNKNOWN

Phase 1 の読み取り実装は `src/kihachi_mcp/services/jarvis/` にある。Studio の JARVIS 欄は `/api/jarvis/turn` を呼ぶ。書き込みは `APPROVAL_REQUIRED` で止まり、実行しない。\
Live write / Save / Export は、人の明示指示があるまで行わない。LIVE VERIFICATION PENDING。

---

## Boundary

| 層 | 分類 | 置く場所 |
|---|---|---|
| Studio UI | EXISTS | `studio/app.py` + `studio/static/index.html` を拡張する。新アプリは作らない |
| Jarvis | CREATE | `services/` 配下の新規モジュール。Studio プロセス内。ソケットも UDP も直接開かない |
| Brain | EXISTS | `api/brain.py` と Studio の `ProductionBrief` 経路。Jarvis と混ぜない |
| kihachi-music-ai | UNKNOWN | import が無い。接続は Phase 5 で差分を見てから |
| MCP / Gateway | EXISTS | ツールを増やして Jarvis 専用サーバーにしない。Jarvis は既存関数を呼ぶ |
| AbletonGPT | EXISTS | `gateway/client.py` 経由のみ |
| Max for Live | EXISTS | `StudioRuntime` 経由のみ |
| Ollama | EXISTS | `OllamaClient` を再利用。新クライアントは作らない |

---

## Phase 1 — Jarvis Foundation（read-only）

目標: 「今のプロジェクトどうなってる？」に、開いている候補と、取得できた Live 読み取りだけを返す。書けない。

| 項目 | 分類 | 内容 |
|---|---|---|
| Intent スキーマ | CREATE | `type`, `target`, `scope` or `range`, `requested_action`, `write_requested`。brief 用 `STUDIO_SCHEMA` とは別。書き込み Intent は Plan で止める |
| Intent 抽出 | EXTEND | 第一段は決定的規則（`StudioDialogue` と同じく実行しない）。Ollama を使う段は `OllamaProvider.generate_structured` + 既存 JSON 検証。失敗は Intent を作らず UNKNOWN |
| Plan スキーマ | CREATE | `goal`, `reads`, `proposed_writes`, `verification`, `requires_human_approval`, `risk_level`。`proposed_writes` が空でない Plan は実行関数を持たない |
| 状態機械 | CREATE | IDLE, UNDERSTANDING, READING_STATE, PLANNING, PROPOSED, APPROVAL_REQUIRED, APPROVED, EXECUTING, VERIFYING, VERIFIED, HUMAN_AUDITION_REQUIRED, COMPLETED, FAILED, UNKNOWN。Phase 1 で遷移するのは読み取り側だけ（EXECUTING へは行かない） |
| Context builder | EXTEND | `project_view`、候補の承認フラグ、`ollama_status`、Live health。取得失敗はフィールドを UNKNOWN |
| Read-only 実行 | EXISTS | Gateway 読み取り関数と Studio の inspector。新規の Live コマンドは足さない |
| 候補承認 | EXISTS | `StudioRuntime.approve`。Phase 1 では呼ばない |
| ApprovalGate | EXISTS | Phase 1 では呼ばない |
| Gateway `confirm=true` | EXISTS | Phase 1 では書き込み関数を呼ばない |

完了条件: ユニットテストで、読み取り Intent が構造化される、Plan に write が無い、状態が READING_STATE から PROPOSED または FAILED/UNKNOWN になる、UNKNOWN が COMPLETED にならない。

---

## Phase 2 — Studio Integration

| 領域 | 分類 | 内容 |
|---|---|---|
| JARVIS CONSOLE | EXTEND | 既存「KIHACHIと対話」セクション。`/api/dialogue/turn` の隣に Jarvis 状態と構造化応答を足す。対話の Live 拒否は残す |
| PROJECT STATE | EXTEND | 既存「プロジェクト」セクション。`project_view` に無い項目は UNKNOWN と表示する |
| AI PLAN | CREATE | Plan の表示。Goal / Reads / Writes / Risk / Status。実行ボタンは Phase 3 まで出さない |
| ABLETON MONITOR | EXTEND | 既存診断と Live health。connection, transport, song time, tracks は既存読み取りがあるものだけ。無い項目は UNKNOWN |
| APPROVAL CENTER | CREATE | Phase 3 まで空、または「書き込みは未接続」 |
| Voice | EXISTS | `voice_io` のテキストを同じ Intent 入口へ渡すのは Phase 6。Phase 2 では現状の対話のまま |

新規 HTML アプリ、新規ポートは作らない。

---

## Phase 3 — Approval

| 項目 | 分類 | 内容 |
|---|---|---|
| 候補承認 | EXISTS | Take 採用の意味のまま残す |
| ApprovalGate | EXTEND | 操作 Plan のハッシュに人が出したトークンを結ぶ。Runtime が無条件に `approve()` する経路は、Jarvis 経由の書き込みでは使わない |
| APPROVE / REJECT UI | CREATE | 既存 Live セクションに pending を出す。REJECT はトークンを作らない |
| Gateway 書き込み | EXTEND | `confirm=true` 単独では Jarvis 経路から呼べないようにする。ゲート通過後だけ既存関数を呼ぶ |
| 破壊的操作 | EXISTS | カタログの `RISK_DESTRUCTIVE` と `destructiveHint`。人の承認無しは失敗、をテストで固定する |
| 二重ゲートの新設 | REMOVE DUPLICATE | 新しいトークン庫は作らない |

書き込みの実機実行は、このフェーズのテストが fake / mock で緑になったあと、人が 1 回だけ指示したとき。

実装状況: パート計画の承認と拒否は既存の `ApprovalGate` に記録する。Jarvis の `request_gateway_write` は `confirm=true` だけではツールを呼ばない。承認済みでも Studio からは `execute=False` のままなので、Live には送らない。Live セクションの `live-plan-pending` は、確認した計画を保留として出し、拒否ではトークンを作らない。候補が無いあいだ、Live への適用ボタンは出さない。Jarvis の「承認」欄の「保留中の操作」は、採用待ちの修正案と、承認済みで未送信のパート計画、実機の Set に合わせ済みで2回目の承認を待つパート計画を `/api/approvals` から一覧にする。期限（15分）が切れた計画は出さない。修正案はそこで採用・却下できる。中身は既存の `ApprovalGate` と保留中の修正案だけで、新しい記録は作らない。

実機への送信（2026-10-03 に人が指示）: 「実機の Live に送る」は、承認済みの計画で実機の Set を読み、トラックとシーンを末尾に置き直す。ハッシュが変わるので、合わせ直した古い計画は保留から消す。もう一度承認したあとの 2 回目の押下で、`StudioRuntime` の既存の実行器から 1 回だけ送って読み戻す。再生中・録音中は承認を残して止める。別の適用が走っているときも承認を残す。読んだあとに Set が変わったとき、同じ名前のトラックがあるとき、15分切れは送らない。保留一覧に残り時間を出す。押すのは人で、保存はしない。

---

## Phase 4 — Verification Loop

| 項目 | 分類 | 内容 |
|---|---|---|
| Readback 比較 | EXISTS | `readback_verification`, `LiveExecutionReceipt` |
| Jarvis からの順序 | CREATE | EXECUTING のあと必ず VERIFYING。比較不能は UNKNOWN。`verified` を COMPLETED にしない場合は HUMAN_AUDITION_REQUIRED または FAILED |
| 音楽的成功 | EXISTS | 既存文言どおり、readback 一致は「Live が計画どおり応答した」だけ。品質完了とは書かない |
| 失敗の昇格禁止 | CREATE | テスト: 検証失敗 → COMPLETED にならない。UNKNOWN → SUCCESS にならない |

LIVE VERIFICATION PENDING のまま完了扱いしない。

実装状況: 承認待ちの書き込みに対する `settle_readback` は、EXECUTING の次を VERIFYING にする。一致しても COMPLETED にはせず、人が聴く状態で止める。不一致と比較不能は完了にしない。二つの Live 読み取りでテンポかトラック数が違えば UNKNOWN とし、成功にしない。未読のパート計画は「読み戻しを確認」で偽の Set に合わせ直し、前の承認を無効にします。もう一度承認したあとだけ、偽の Live が計画どおり応答したと返します。音の完成にはせず、実機は呼びません。

---

## Phase 5 — Brain

| 項目 | 分類 | 内容 |
|---|---|---|
| 評価・改変の音楽判断 | EXTEND | 「もっと Dub っぽく」は MCP コマンドにしない。既存 `production_review` / `ProductionBrief` / `Brain.review_song` へ渡す |
| 実行計画への変換 | CREATE | Brain の案を Jarvis Plan にする。Brain は Live を書かない |
| kihachi-music-ai | UNKNOWN | 接続するかは、mcp 内 SongSpec との差分をコードで見てから。今は呼ばない |
| ACE-Step | EXISTS（別リポジトリ） | 本計画の実行経路に入れない |

実装状況: 「もっと Dub っぽく」は `musical_judgment` になり、SongSpec があるときだけ `Brain.review_song` に渡す。指摘と提案は Jarvis の計画 `proposal` になる。書き込みリストは空のまま、Live は呼ばない。kihachi-music-ai と ACE-Step は呼ばない。

Jarvis の変更案（例:「33-48小節のハットを減らして」）は、「この変更案を修正案にする」で既存の修正案になる。できるのは、指定したパートの音を範囲内で1つおきに抜くか、消すこと。増やす変更はハットだけで、2つの音の間に弱めの音を足す。「前半」「後半」「リエントリー」は、開いている曲の小節数とセクションから範囲を決める。評価の質問（例:「後半のハット多くない？」）には、曲データの小節ごとのノート数（`bar_notes`）から、範囲内と範囲外の1小節あたりの音数を返す。修正案は新しい候補で、採用するかは人が決める。Live は変えない。

Session MIDI クリップの読み取り・評価・改訂プレビュー（`services/jarvis/clip_review.py`）:
- ユーザーが指定した 1 つのトラック（`track_index` または `track_name` の片方のみ）と 1 つの Session クリップ（`clip_index` >= 0）を `get_midi_clip_notes` だけで読み取る。この経路では他の Live 接続を使わない。
- 指定が曖昧・重複・欠落・無効な場合、または対象が Session MIDI クリップでない場合は推測せず `UNKNOWN` とし、成功扱いしない。
- `get_midi_clip_notes` が `complete=false`、`status="PARTIAL"`、`truncated=true`（4096 ノート超の切り詰めなど）、形式不正ノート、または途中失敗を返した場合は、評価と改訂プレビュー作成を直ちに止め、`UNKNOWN`（`ok: false`）とする。
- 評価（`clip_evaluation`）は取得したノートから計算できる客観事実（ノート数、拍長、小節数、小節ごとのノート数 `bar_notes`、1小節あたり平均ノート数、音域 `pitch_range`、ユニークピッチ、平均/最小/最大ノート長、ベロシティ最小/最大/平均、オフビート数、ゴーストノート数）だけを返す。音色（`timbre`）や実際の聴感品質（`perceived_audio_quality`）は `UNKNOWN` のままとし、`musical_quality_claimed: false` を守る。
- 改訂案（`clip_preview`）は `original_notes` を無変更のまま保持した非破壊プレビュー（`preview_notes`、`note_count_before`、`note_count_after`、`bar_notes_before`、`bar_notes_after`、`applied_to_live: false`、`live_changed: false`）として返す。Live への書き戻し、MIDI 置換、承認、再生、保存、書き出しは今回の作業範囲に含めない（LIVE VERIFICATION PENDING）。

---

## Phase 6 — Voice

| 項目 | 分類 | 内容 |
|---|---|---|
| STT / TTS | EXISTS | `voice_io` |
| 別の音声ロジック | REMOVE DUPLICATE | 作らない。テキストと同じ Intent → Plan → Approval |
| プロバイダ選定 | EXISTS | クラウドフォールバックは現コードに無い。変えない |

実装状況: 聞き取り結果は制作指示のテキスト欄に入り、「聞く」と同じ `/api/jarvis/turn` を通る。別の意図解釈は無い。読み取りは `ableton_ping`、`ableton_status`、`ableton_list_tracks` だけを、渡されたクライアントに対して呼ぶ。承認済み計画の実行ヘルパーは `FakeLiveTransport` 以外を拒否する。

---

## Tests

既存スイートは残す。追加は Jarvis モジュールに対するもの。

| 区分 | 分類 | 最低ケース |
|---|---|---|
| Intent | CREATE | 評価文は `write_requested: false`。範囲つき変更文は true。壊れた JSON は UNKNOWN |
| Plan | CREATE | write が一つでもあれば `requires_human_approval: true` |
| 状態 | CREATE | 許可された遷移だけ。読み取り失敗は FAILED または UNKNOWN |
| 承認 | EXTEND | トークン無しの書き込みは失敗。計画改変後の古いトークンは失敗（`ApprovalGate` の既存テストを壊さない） |
| 検証 | EXTEND | 不一致は verified にならない（既存 receipt テストを再利用） |
| Studio → Jarvis | CREATE | HTTP は既存 `StudioApp` のテストスタイル |
| Jarvis → Brain | EXTEND | 音楽判断 Intent が Live 関数を呼ばない |
| Jarvis → MCP 関数 | EXTEND | 読み取りは既存 gateway 関数。モッククライアント |
| Approval → Execution | CREATE | fake transport（`live_transport_fake.py`）のみ |
| 安全 | CREATE | 未承認 write 失敗、UNKNOWN を成功にしない、検証失敗を COMPLETED にしない、破壊的操作は承認必須 |

実 Ableton の smoke は自動テストに入れない。必要になったら止めて人に頼む。

---

## Definition of Done（この計画の完了時）

現状（2026-10-03）: 1〜15 はコードとテストで満たした。全スイートは 806 件が通る（`uv run python -m pytest`。macOS アプリの 2 ファイルは `uv run pytest` だと `scripts` を読めず収集エラーになる）。7 と 8 は偽の Set でだけ確かめた。パート計画の実機送信は、人が「実機の Live に送る」を押したときだけ行う。実機ではまだ試していない（LIVE VERIFICATION PENDING）。Astra のジャンル・雰囲気・指示は方向の記録であり、計画確認のノートは変えない。

1. 既存 Studio を使う — 計画上 EXISTS。実装後も新 UI を作らない
2. Jarvis Console が Studio 内にある — Phase 2
3. 実 Project State — Phase 1 の context。Live 実機は別
4. Intent が構造化データ — Phase 1
5. Plan 生成 — Phase 1
6. Write は承認ゲート — Phase 3
7. 承認済みだけ実行 — Phase 3
8. 実行後に検証 — Phase 4
9. UNKNOWN を正しく扱う — Phase 1 から
10. Brain / MCP / AbletonGPT の分離 — この境界を守る
11. Unit green — 各 Phase
12. Integration green — Phase 2 以降
13. ruff — 変更のたびに `uv run ruff check src tests`
14. ドキュメント — 本ファイルと監査を、実装に合わせて更新
15. 重複を残さない — Gateway の裸の `confirm` を Jarvis 経路から外す

実機で Live を見ていない間は、完了報告に `LIVE VERIFICATION PENDING` と書く。

---

## Explicitly not in this plan

- `ai-company-os/` の接続
- 新しい MCP サーバー
- 新しい Ollama クライアント
- 新しい承認ストレージ
- Save、Export（人の明示指示まで）
- Live への write は、パート計画の「実機の Live に送る」だけ（人が押す）
