# CURRENT SYSTEM STATUS

確認日: 2026-09-28 / 環境: Intel Mac（i9-9980HK, 32 GB）/ Python 3.14 / Ableton Live 12.4.5 Suite / Ollama `gemma4:latest`

## 部品

| 部品 | 状態 | 根拠 |
| --- | --- | --- |
| kihachi-mcp（アプリ層） | READY | `uv run pytest -q` 532 passed |
| 制作画面 | READY | ブラウザで生成・レビュー・修正・承認・ドライラン・検証・診断を操作。画面エラーなし |
| CLI `kihachi` | READY | create / projects / inspect / review / revise / approve / ableton plan・dry-run・execute・verify / doctor / start |
| MCP ツール | READY | 29 ツール（新規 11: create_song, list_projects, get_project, review_song, revise_song, approve_song, dry_run_ableton_plan, execute_ableton_plan, verify_ableton_project, ollama_status, doctor） |
| Ollama | READY | 実モデルで構造化解釈 約58〜63秒 |
| Ollama 停止時 | READY | 既定解釈で生成が完了し、doctor は OFFLINE と対処を表示 |
| Ableton Live（KIHACHI Device） | READY | 実機スモークテスト PROJECT READY |
| AbletonGPT Remote Script | WARNING | 9877 は開いているが ping に応答しない（Live 側のダイアログ等が原因の可能性）。キット読み込み以外は影響なし |

## シナリオ

| シナリオ | 結果 | 備考 |
| --- | --- | --- |
| Golden Path 1（122 BPM / Dm / 64小節 / Tech House・Dub Techno） | PASS（fake Live） | ジャンルは先頭の tech_house のみ使用 |
| KIHACHI Golden Path（110 BPM / D#m / Mutation Funk × Dub × Tech House / Swing 54% / 約5分） | PASS（fake Live・実Ollama画面生成） | 136小節 4.95分、SLAP BASS / DUB CHORDS / MUTATION SYNTH、Break あり、ゴーストノート、オクターブ移動、54% スイング |
| Ollama E2E（オンライン / オフライン） | PASS | オンラインは実モデル、オフラインは到達不能URLで確認 |
| 実機スモークテスト（120 BPM / Cm / 32小節） | PASS | Tempo 120 / Tracks 4 / Clips 16 / Notes 576 すべて PASS、Arrangement SKIP（未展開） |

## 既知の制限

- パートは Kick / Hats / Bass / Stab（+ Mutation Funk の Lead）の最大5つ。依頼書の11パートには未対応。
- 複数ジャンル指定は先頭ジャンルだけを使い、画面に明記します。
- 「4つ打ちKick」「Tight snare」「Vocoder」など、規則で読めない語は「未反映」と表示します（音に入ったとは言いません）。
- Memory / Knowledge は MCP にありますが、制作画面にはまだ出していません。
- 実機でのアレンジメント展開の読み戻しは今回未実施（fake Live では PASS）。
- 音楽的な良し悪しは検証対象外です。`PROJECT READY` は Live が計画どおり応答したことだけを意味します。
