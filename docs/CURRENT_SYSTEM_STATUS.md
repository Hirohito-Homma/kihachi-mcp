# CURRENT SYSTEM STATUS

確認日: 2026-09-29 / 環境: Intel Mac（i9-9980HK, 32 GB）/ Python 3.14 / Ableton Live 12.4.5 Suite / Ollama `gemma4:latest` / KIHACHI Live Device 0.3.5

## 部品

| 部品 | 状態 | 根拠 |
| --- | --- | --- |
| kihachi-mcp（アプリ層） | READY | `uv run pytest -q` 564 passed、ruff 通過 |
| 制作画面 | READY | 生成・レビュー・修正・承認・ドライラン・検証・エフェクト・つまみの設定し直し・MIX・サイドチェイン・マスタリング・音圧の測定・診断を操作 |
| CLI `kihachi` | READY | create / projects / inspect / review / revise / approve / ableton plan・dry-run・execute・verify・effects・mix・sidechain・retune・master / measure / doctor / start |
| MCP ツール | READY | 35 ツール（apply_ableton_effects / mix / sidechain / retune / master、measure_loudness を追加） |
| Ollama | READY | 実モデルで構造化解釈 約58〜63秒 |
| Ollama 停止時 | READY | 既定解釈で生成が完了し、doctor は OFFLINE と対処を表示 |
| Ableton Live（KIHACHI Device 0.3.5） | READY | 実機でトラック・エフェクト・MIX・サイドチェイン・マスターの送信と読み戻しが verified |
| AbletonGPT Remote Script | WARNING | 9877 は開いているが ping に応答しない。キット読み込み以外は影響なし |

## シナリオ

| シナリオ | 結果 | 備考 |
| --- | --- | --- |
| Golden Path 1（122 BPM / Dm / 64小節 / Tech House・Dub Techno） | PASS（fake Live） | ジャンルは先頭の tech_house のみ使用 |
| KIHACHI Golden Path（110 BPM / D#m / Mutation Funk × Dub × Tech House / Swing 54% / 約5分） | PASS（fake Live・実Ollama画面生成） | 136小節 4.95分、Break あり、ゴーストノート、オクターブ移動、54% スイング |
| Ollama E2E（オンライン / オフライン） | PASS | オンラインは実モデル、オフラインは到達不能URLで確認 |
| 実機スモークテスト（120 BPM / Cm / 32小節） | PASS | Tempo / Tracks 4 / Clips 16 / Notes 576 すべて PASS |
| 実機 エフェクト（TEST008, 12トラック） | verified | 108 操作。独立した読み戻しで 83 設定すべて誤差 0 |
| 実機 MIX | verified | 12 操作（音量 dB・パン） |
| 実機 サイドチェイン | verified | 21 操作。Sub・Bass・Pad のキーが「KIHACHI Kick … [KIHACHI]」（Post FX）と読み戻し |
| 実機 マスタリング | verified | 19 操作で追加、以後つまみの設定し直し（14 操作）を3回 |
| 実機 キック EQ の設定し直し | verified | 10 操作（55Hz を −3 dB）。デバイスの追加・削除なし |
| 音圧（TEST008 書き出し7回） | 測定済み | −16.5 → −12.2 LUFS、True Peak −1.19 dBTP、LRA 4.6 LU。サビ −11.3、Break −16.6 |

## 音圧の経過（TEST008）

| 書き出し | 変更 | Integrated | True Peak |
| --- | --- | --- | --- |
| 1 | マスタリングまで | −16.5 LUFS | −1.02 |
| 2 | サイドチェイン追加 | −16.7 | −1.02 |
| 3 | Sub / Bass を −3 dB | −16.9 | −1.03 |
| 4 | キック 55Hz を −3 dB | −18.0 | −1.26 |
| 5 | Limiter Input Gain +10.8 dB | −13.6 | −1.01 |
| 6 | Limiter Input Gain +15.6 dB | −12.0 | −0.99（上限超過） |
| 7 | Limiter Ceiling −1.2 dB | −12.2 | −1.19 |

低域の大半はキックの基音（約55Hz）でした。6 では +4.8 dB 上げても音圧は +1.6 dB しか上がらず、それ以上はリミッターで潰れるだけと判断して止めています。

## 既知の制限

- パートは15種類（Kick / Snare / Hats / OpenHat / Perc / Sub / Bass / Stab / Pad / Arp / Guitar / Horn / Lead / Vocal / FX）。どれが鳴るかはジャンルと指示で決まります。
- 複数ジャンル指定は先頭ジャンルだけを使い、画面に明記します。
- 音圧はクラブ目安（−8〜−6 LUFS）に届いていません（−12.2 LUFS）。さらに上げるにはリミッター以外（マスターの Saturator など）の調整が必要です。
- Memory / Knowledge は MCP にありますが、制作画面にはまだ出していません。
- 実機でのアレンジメント展開の読み戻しは未実施（fake Live では PASS）。
- 音楽的な良し悪しは検証対象外です。`verified` / `PROJECT READY` は Live が計画どおり応答したことだけを意味します。
