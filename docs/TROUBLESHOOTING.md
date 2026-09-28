# TROUBLESHOOTING

まず `uv run kihachi doctor`（または制作画面の「システム」→「診断を実行」）を実行してください。各行に原因と対処が出ます。

| 症状 | 原因 | 対処 |
| --- | --- | --- |
| `Ollama: OFFLINE` | Ollama が起動していない / URL が違う | `ollama serve`。URL は「システム → AI」か `KIHACHI_OLLAMA_URL`。既定解釈でも制作は続けられます |
| `Model OFFLINE` / 保存できない | モデルが未導入 | `ollama list` にあるモデルだけ選べます。KIHACHI はダウンロードしません（[OLLAMA_SETUP.md](OLLAMA_SETUP.md)） |
| 生成に1分近くかかる | ローカルモデルの推論時間 | 正常です（Intel i9 で約50〜60秒）。「キャンセル」で止められます |
| 「AIの応答が2回とも使えなかった」 | モデルの JSON が崩れた | 明示値と既定値で作られています。別モデルを試すか、そのまま使えます |
| `Live: OFFLINE` | デバイス未配置 / 返信ポート 17772 を別プロセスが使用 | Live にデバイスを置く。Cursor の MCP サーバーが 17772 を掴んでいれば止める |
| デバイス版が一致しない | Max 側の JS が古い | `maxforlive/kihachi.device.js` を更新し、Max で `reload` |
| `AbletonGPT: WARNING`（応答しません） | Remote Script はつながるが Live のメインスレッドが応答しない | Live のダイアログ（保存確認・環境設定など）を閉じる。キット読み込み以外の制作には影響しません |
| 「まだ承認されていません」 | 承認前に送信しようとした | 「この候補を承認」→「適用内容を確認」 |
| 「すでに送信を試行済み」 | 同じ候補の再送 | 重複配置を防ぐ仕様です。直したい場合は修正案を採用して新しい候補を送ってください |
| `VERIFICATION FAILED` | Live の内容が計画と違う | どの行が FAIL かを見て、[RECOVERY.md](RECOVERY.md) の手順で戻す |
| `EXTERNAL VERIFICATION REQUIRED` | Live に接続できず読み戻せない | Live とデバイスを確認して「Liveから読み戻して検証」 |
| 画面上部に赤い帯 | サーバー側のエラー | 「再試行」。続く場合は「診断」。詳細は「開発者モード」で表示 |
| CLI で「制作画面が起動していません」 | Live 系コマンドは起動中の制作画面を通す | `uv run kihachi start` |
| ポート 8765 が使用中 | 制作画面が既に起動している | `uv run kihachi start` は既存の画面を開きます |

## ログと秘密情報

- 画面・CLI・ログには API キーやセッショントークンを出しません。
- 設定ファイル `~/Library/Application Support/KIHACHI/settings.json` にはプロバイダ・URL・モデル名だけを保存します。
