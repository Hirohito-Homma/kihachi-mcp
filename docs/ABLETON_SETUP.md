# ABLETON SETUP

KIHACHI は Max for Live の KIHACHI Live Device を通して Live を操作します。AbletonGPT の Remote Script は任意で、キットやプリセットの読み込み補助に使います。

## 必要なもの

- Ableton Live 11 以降（Suite または Max for Live 付き）。開発時の実機は Live 12.4.5 Suite / Intel Mac。
- KIHACHI Live Device（`kihachi-live-device/0.3.1`）。作り方は [maxforlive/README.md](../maxforlive/README.md)。`.amxd` はリポジトリに含めていません。

## 手順

1. Live を起動し、Set を開きます（KIHACHI は Set を保存しません。必要なら自分で保存してください）。
2. KIHACHI Live Device を任意のトラックへ1つ置きます。
3. `uv run kihachi start` で制作画面を開き、上部が `Live: READY — Live 12.x / kihachi-live-device/0.3.1` になることを確認します。
4. 任意: AbletonGPT の Remote Script を Live の「環境設定 → Link/Tempo/MIDI → コントロールサーフェス」で有効にします。上部に `AbletonGPT: READY` と出れば使えます。

## 通信

| 経路 | ポート | 用途 |
| --- | --- | --- |
| Studio → KIHACHI Live Device | UDP 127.0.0.1:17771 | 操作の送信 |
| KIHACHI Live Device → Studio | UDP 127.0.0.1:17772 | 返信 |
| Studio → AbletonGPT Remote Script | TCP 127.0.0.1:9877 | キット読み込み（任意） |

すべてループバックです。ポートは `KIHACHI_LIVE_BRIDGE_PORT` / `KIHACHI_LIVE_REPLY_PORT` / `KIHACHI_ABLETONGPT_PORT` で変えられます。セッショントークンは `~/Library/Application Support/KIHACHI/live-bridge.json` にあり、画面・ログ・Git には出しません。

## 送信の保護

- 承認していない候補は送れません。
- 送る前にドライランで操作数を確認します。Live は変わりません。
- 送信は1回だけ。タイムアウトしても自動再試行しません（重複配置を防ぐため、同じ候補は再送できません）。
- 追加するのは `[KIHACHI]` 印の付いた新しいトラックだけです。既存のトラック・クリップは変更しません。
- 再生中・録音中の構造変更は拒否します。
- 送信後、Live から Tempo / Tracks / Clips / Notes（展開後は Arrangement）を読み戻して計画と比べます。一致したときだけ `PROJECT READY` です。

## 実機スモークテスト

```bash
uv run kihachi start          # 別ターミナル
./scripts/verify_live.sh      # 120 BPM / Cマイナー / 32小節。送信前に y/N を聞きます
```

2026-09-28 の実機結果（Live 12.4.5 / Intel Mac）: Tempo 120 PASS、Tracks 4 PASS、Clips 16 PASS、Notes 576 PASS、Arrangement SKIP（未展開）→ `PROJECT READY`。

失敗時の戻し方は [RECOVERY.md](RECOVERY.md)。
