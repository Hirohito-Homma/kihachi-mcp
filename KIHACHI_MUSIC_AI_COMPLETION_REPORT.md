# KIHACHI MUSIC AI COMPLETION REPORT

確認日: 2026-09-28 / Intel Mac（i9-9980HK, 32 GB）/ Ableton Live 12.4.5 Suite

## STATUS

制作画面から、日本語の指示で曲の候補を作り、レビュー・修正・承認・ドライランを経て Ableton Live へ1回だけ送り、Live から読み戻して検証するまでが動きます。実機スモークテストは `PROJECT READY`。音楽的な完成度は検証対象外です。

## Repositories

| Repository | Branch | Commit |
| --- | --- | --- |
| kihachi-mcp（本体・正本） | `feature/issue-0020-ableton-live-automation` | HEAD `0c88f20`。今回の変更は未コミット（下記 Remaining External Actions） |
| AbletonGPT（https://github.com/Hirohito-Homma/AbletonGPT0.2） | `main` | `5fcf063`（変更なし） |
| KIHACHI MUSIC AI（https://github.com/Hirohito-Homma/KIHACHI.git） | `wip/explicit-bar-count` | `25b044f`（変更なし） |

## Commits

未実施（ユーザー確認待ち）。

## PRs

未作成（ユーザー確認待ち）。

## Studio

| 項目 | 内容 |
| --- | --- |
| Location | `src/kihachi_mcp/studio/`（`app.py`, `static/index.html`） |
| Framework | Python 標準ライブラリ HTTP サーバー + 静的 HTML / 素の JavaScript（既存のまま） |
| Build | 不要（ビルド工程なし） |
| Tests | `tests/test_studio_app.py`（実 HTTP サーバーで全フロー・403・トレースバック非表示・画面が呼ぶ API の存在）、`tests/test_studio_workflow.py` |
| Backend Integration | `StudioRuntime` を画面・CLI・MCP で共有。新 API: projects / project / review / revision / approve / ableton plan・dry-run・send・verify / settings / ollama / diagnostics |
| Status | READY（ブラウザで生成〜検証・診断を操作、画面エラーなし） |

## Ollama

| 項目 | 内容 |
| --- | --- |
| Version | 0.34.4 |
| Models | `gemma4:latest` |
| Selected Model | `gemma4:latest`（導入済みモデルのみ選択可。自動ダウンロードなし） |
| Structured Output | JSON 解析 → 修復（コードフェンス・前後文） → スキーマ検証 → 合計2回まで → 既定解釈にフォールバック。キャンセルは再試行しない |
| Status | READY（実モデルで約58〜63秒/回）。停止時も制作画面は落ちず既定解釈で生成 |

## kihachi-mcp

| 項目 | 内容 |
| --- | --- |
| Services | StudioRuntime、AIProvider（Ollama / deterministic）、studio_interpreter、midi_candidate_builder、production_review / revision / workspace、studio_workflow（状態語・読み戻し比較・設定）、diagnostics、live_execution_service + approval gate |
| MCP Tools | 29（新規 11: create_song, list_projects, get_project, review_song, revise_song, approve_song, dry_run_ableton_plan, execute_ableton_plan, verify_ableton_project, ollama_status, doctor） |
| CLI | `kihachi` start / doctor / create / projects / inspect / review / revise / approve / ableton plan・dry-run・execute・verify |
| Status | READY |

## AbletonGPT

| 項目 | 内容 |
| --- | --- |
| Connection | Remote Script TCP 127.0.0.1:9877 は接続可能、ping は無応答 → WARNING と表示 |
| Execution | Live への書き込みは KIHACHI Live Device（UDP 17771/17772）経由。AbletonGPT はキット読み込み補助（任意） |
| Verification | KIHACHI Device 経由の読み戻しで実施 |
| Status | WARNING（KIHACHI の送信・検証には影響なし） |

## Golden Path 1

PASS（122 BPM / Dm / 64小節、fake Live で送信→読み戻し PROJECT READY）。複数ジャンルは先頭 tech_house のみ使用。

## KIHACHI Golden Path

PASS（110 BPM / D#m / mutation_funk / Swing 54% / 136小節 4.95分 / SLAP BASS・DUB CHORDS・MUTATION SYNTH / Break あり / ゴーストノート・オクターブ移動・54%オフビートを MIDI で確認。fake Live で Arrangement まで PASS。実 Ollama で画面から生成も確認）

## Real Ableton

PASS（120 BPM / Cマイナー / 32小節。Tempo 120 PASS / Tracks 4 PASS / Clips 16 PASS / Notes 576 PASS / Arrangement SKIP（未展開）→ PROJECT READY。ユーザー承認のうえ1回だけ送信）

## Tests

| Passed | Failed | Skipped |
| --- | --- | --- |
| 532 | 0 | 0 |

`uv run ruff check src tests`: 既存の `tests/test_drum_samples.py` の I001 のみ（今回の変更外）。

## Known Limitations

- パートは最大5つ（Kick / Hats / Bass / Stab / Lead）。依頼書の11パートは未対応。
- 複数ジャンル指定は先頭だけを使用（画面に明記）。
- 「4つ打ちKick」「Tight snare」「Vocoder」など規則で読めない語は「未反映」と表示（音に入ったとは言わない）。
- Memory / Knowledge は MCP のみで、制作画面には未表示。
- AbletonGPT Remote Script が ping に応答しない。
- 実機でのアレンジメント展開後の読み戻しは未実施（fake Live では PASS）。

## Remaining External Actions

- コミット・プッシュ・PR 作成（ユーザー確認後。`*.asd`、`.cursor/mcp.json`、`.gitignore`、`.codex/`、`ai-company-os/` は含めない）。
- Live のダイアログを閉じて AbletonGPT の ping 応答を確認。
- 必要なら実機でアレンジメント展開 →「Liveから読み戻して検証」で Arrangement 行を確認。

# HOW TO USE KIHACHI MUSIC AI STUDIO NOW

1. ターミナルで `./scripts/setup.sh` を実行します（初回のみ）。
2. Ollama を起動します（任意。なくても制作できます）。
3. Ableton Live を開き、KIHACHI Live Device をトラックに置きます。
4. `uv run kihachi start` を実行すると、ブラウザで制作画面が開きます。
5. 上部に `Live: READY` と出ていることを確認します。
6. 制作指示に「110 BPM、D# minor。Mutation Funk。Swing 54%。約5分。」のように書き、「候補を生成」を押します。
7. 「プロジェクト」で構成とトラック、「AIレビュー」で指摘を確認し、必要なら修正案を「採用」します。
8. 「この候補を承認」を押します。
9. 「適用内容を確認」で送る内容を見てから、「この内容でLiveへ適用」を押します（1回だけ送信）。
10. `ABLETON VERIFICATION` が `PROJECT READY` なら、Live の KIHACHI トラックで再生して確かめます。
