# KIHACHI MUSIC AI COMPLETION REPORT

確認日: 2026-09-29 / Intel Mac（i9-9980HK, 32 GB）/ Ableton Live 12.4.5 Suite

## STATUS

制作画面から、日本語の指示で曲の候補を作り、レビュー・修正・承認・ドライランを経て Ableton Live へ1回だけ送り、Live から読み戻して検証するまでが動きます。続けてエフェクト・MIX・サイドチェイン・マスタリングを同じ「確認 → 1回だけ送信 → 読み戻し」で行い、書き出した WAV の音圧を測れます。実機では全段階が `verified`、TEST008 は −12.2 LUFS / True Peak −1.19 dBTP。音楽的な完成度は検証対象外です。

## Repositories

| Repository | Branch | Commit |
| --- | --- | --- |
| kihachi-mcp（本体・正本） | `feature/studio-integration`（`0c88f20` から分岐） | 下記 Commits |
| AbletonGPT（https://github.com/Hirohito-Homma/AbletonGPT0.2） | `main` | `5fcf063`（変更なし） |
| KIHACHI MUSIC AI（https://github.com/Hirohito-Homma/KIHACHI.git） | `wip/explicit-bar-count` | `25b044f`（変更なし） |

## Commits

| Commit | 内容 |
| --- | --- |
| `d35afa9` | ジャンル知識・スイング・ドラムサンプルをノートに反映 |
| `6eb5922` | 参考音源ライブラリとサンプル索引 |
| `fbb3194` | オンデバイス短時間音声入力と対話 |
| `460ad63` | 制作画面を制作の中枢に（AI provider、レビュー・修正・承認、ドライラン、送信、読み戻し検証、状態・設定・診断） |
| `813dc61` | `kihachi` CLI と MCP ツール（共有 StudioRuntime） |
| `ec24140` | セットアップ・画面・Ollama・Ableton・現状の文書とスクリプト |
| `16af465` `7140bbb` | パートを15種類に（Snare / OpenHat / Perc / Sub / Pad / Arp / Vocal / FX / Guitar / Horn を追加） |
| `118f238` `a8a6baf` | パートごとのエフェクトと音色（実機で読んだつまみの目盛りから変換）、読み戻し検証 |
| `a28a958` | MIX（音量 dB・パン、`set_track_mixer`） |
| `d8a4429` `2e7b1dc` | マスタートラックへのクラブ向けマスタリング、段階つまみの補正、設定し直し |
| `20978ff` `01ea152` | 書き出した WAV の音圧測定（BS.1770 LUFS・True Peak・LRA・低域比率・セクション別） |
| `413c99d` | キックをキーにした Sub・Bass・Pad のサイドチェイン（`set_sidechain_source`） |
| `4af4446`〜`a6b97cb` | 測定に基づく調整（Sub/Bass −3 dB、キック 55Hz −3 dB、Limiter Input Gain +15.6 dB・Ceiling −1.2 dB）と、パートのエフェクトのつまみの設定し直し |

## PRs

[#9 Make the KIHACHI Studio the production control centre](https://github.com/Hirohito-Homma/kihachi-mcp/pull/9)（base `main`）。CI は GitHub アカウントの請求問題でジョブが開始されず失敗（`main` も 2026-09-27 から同じ理由で失敗）。同じ手順（`uv sync --locked` / pytest / ruff / `fastmcp list`）を Python 3.12 でローカル実行し、すべて通過。

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
| MCP Tools | 35（新規 17: create_song, list_projects, get_project, review_song, revise_song, approve_song, dry_run_ableton_plan, execute_ableton_plan, verify_ableton_project, ollama_status, doctor, apply_ableton_effects, apply_ableton_mix, apply_ableton_sidechain, apply_ableton_retune, apply_ableton_master, measure_loudness） |
| CLI | `kihachi` start / doctor / create / projects / inspect / review / revise / approve / ableton plan・dry-run・execute・verify・effects・mix・sidechain・retune・master / measure |
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

TEST008（12トラック、KIHACHI Live Device 0.3.5）: エフェクト 108 操作、MIX 12、サイドチェイン 21、マスタリング 19（以後の設定し直し 14 ×3）、キック EQ の設定し直し 10、すべて `verified`。どれもユーザー承認のうえ1回だけ送信。書き出し7回の音圧の経過は `docs/CURRENT_SYSTEM_STATUS.md`。

## Tests

| Passed | Failed | Skipped |
| --- | --- | --- |
| 562 | 0 | 0 |

`uv run ruff check src tests`: すべて通過（既存の `tests/test_drum_samples.py` の import 順も修正）。

## Known Limitations

- パートは15種類。どれが鳴るかはジャンルと指示で決まります。
- 音圧はクラブ目安（−8〜−6 LUFS）に届かず −12.2 LUFS。リミッターをこれ以上上げても潰れるだけでした。
- 複数ジャンル指定は先頭だけを使用（画面に明記）。
- 「4つ打ちKick」「Tight snare」「Vocoder」など規則で読めない語は「未反映」と表示（音に入ったとは言わない）。
- Memory / Knowledge は MCP のみで、制作画面には未表示。
- AbletonGPT Remote Script が ping に応答しない。
- 実機でのアレンジメント展開後の読み戻しは未実施（fake Live では PASS）。

## Remaining External Actions

- GitHub の請求問題を解消し、PR #9 の CI を再実行（有料アカウント操作のため未実施）。
- PR #9 のレビューとマージ。
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
11. Live の再生を止め、「エフェクト処理」→「MIX」→「サイドチェイン」→「マスタリング」の順に、それぞれ確認してから送ります。
12. Live で WAV を書き出し、`uv run kihachi measure <WAVのパス> --project <候補ID>` で音圧を測ります。
