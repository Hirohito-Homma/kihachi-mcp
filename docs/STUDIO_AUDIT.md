# STUDIO AUDIT

対象: 既存の KIHACHI 制作画面（`src/kihachi_mcp/studio/`）。方針は「作り直さず、保持して拡張する」。

## 既存の構成（監査時点）

| 項目 | 内容 |
| --- | --- |
| サーバー | Python 標準ライブラリ `http.server`（ThreadingHTTPServer）、127.0.0.1:8765 |
| 画面 | 静的 HTML 1枚（`static/index.html`）、素の JavaScript、ビルド工程なし |
| デザイン | ダークテーマ。CSS 変数 `--bg` / `--panel` / `--accent: #d7a13b`、`section` / `.pill` / `.metric` / `.grid` / `button.secondary` |
| アプリ層 | `StudioRuntime`（候補の生成・保存・Live 適用・展開・サンプル読み込み） |
| 保存 | `~/Library/Application Support/KIHACHI/candidates/<id>.json` と印ファイル |
| Live | Max for Live デバイス（UDP 17771/17772）、AbletonGPT Remote Script（TCP 9877） |
| 既存機能 | 制作指示→解釈→候補、指示ごとの反映、MIDI 書き出し、適用プレビュー→適用、アレンジメント展開、Drum Rack サンプル、参考音源ライブラリ、音声対話 |

## 監査で見つけた問題と対処

| 問題 | 影響 | 対処 |
| --- | --- | --- |
| `/api/health` が未定義関数 `_system_statuses` で落ちる（前回作業の中断跡） | 画面の状態表示が全滅 | `studio_workflow.system_statuses` を実装 |
| 診断が UDP の Live を TCP 接続で調べていた | Live が常に OFFLINE と表示 | 実行中 Studio の Live 状態を使う |
| Remote Script をポートが開いているかだけで判定 | 応答しないのに PASS | 実際に ping し、無応答は WARNING |
| Ollama の JSON 崩れで生成ジョブが失敗 | 生成が止まる | 修復 → 検証 → 限定再試行 → 既定解釈 |
| 「Swing 54%」がジャンル Swing と一致 | ジャンル誤認 | 数値付きスイングはジャンル扱いしない |
| 承認なしで適用できた | 人間承認の欠落 | 承認印がない候補は送信拒否 |
| 送信成功＝完了扱い | 検証の欠落 | Live から読み戻して Tempo / Tracks / Clips / Notes / Arrangement を比較 |
| 例外でトレースバックが画面に出る | 分かりにくい・情報露出 | 利用者向け説明のみ。詳細は開発者モード |
| 別オリジンからの POST を受け付けた | CSRF | Origin と Host が一致しない POST は 403 |
| 設定の既定 BPM などを環境変数で変えられると表示（実際は未使用） | 誤表示 | 実際の固定既定値を表示。保存先 `KIHACHI_PROJECT_DIR` は実際に効くよう接続 |
| ジャンルが既に書く要素（ゴーストノート等）を「未反映」と表示 | 誤解 | 「ジャンル既定で含む」を追加（読んだとは言わない） |
| キック/ベースの指摘が推奨どおり直しても消えない | 修正ループ | レビューと修正で同じしきい値を共有 |

## 保持したもの

画面の構成・配色・部品・既存ボタンと文言、既存 API（`/api/generate` `/api/apply` など）、参考音源・音声対話・展開・サンプル機能。`/api/apply` は同じ入口のまま承認必須にしました。

## 追加したもの（既存の部品で）

プロジェクト（一覧・SongSpec・構成・トラック）、AIレビュー、修正案（BEFORE/AFTER・採用/却下・履歴）、承認、ドライラン（ABLETON PLAN）、読み戻し検証、システム（診断・AI 設定・Ableton/Project 情報・開発者モード）、エラー帯（再試行 / 設定 / 診断）、状態表示の READY / WARNING / OFFLINE。

## 範囲外

MUSICAI PLAYER。新しい UI フレームワークの導入。
