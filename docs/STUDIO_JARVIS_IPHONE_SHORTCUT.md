# JARVIS iPhone音声入力（Tailscale）

iPhoneのショートカットから、音声認識された文字だけをKIHACHI StudioのJARVISへ送る手順です。音声ファイルは転送しません。JARVISの返答は制作案で、Liveへの自動適用はしません。

## 接続の仕組み

```text
iPhoneショートカット
  ── HTTPS / Tailscale Serve ──▶ Macの127.0.0.1:8766（JARVIS専用入口）
                                      └─ JARVISの返答

Studio本体（127.0.0.1:8765）はTailscale Serveに公開しません。
```

Tailscale Serveはtailnet内だけで使い、Funnelは有効にしません。専用入口はTailscaleの本人確認ログイン名と64文字以上のBearerトークンを両方確認します。送信先は`POST /api/jarvis/shortcut`だけで、他のパスとHTTPメソッドは拒否します。専用サーバーはループバックにのみbindします。

## Macでの起動

MacにTailscaleをインストールしてログインし、iPhoneも同じtailnetに接続します。Tailscale管理画面でHTTPS証明書を有効にします。`KIHACHI_JARVIS_TAILSCALE_USER`には、ショートカットを実行するiPhone側のTailscaleログイン名（通常はメールアドレス）を設定します。MacとiPhoneが別のTailscaleアカウントなら、そのユーザーにMacのServeへのアクセス権が必要です。

Studioのリポジトリで次を実行します。トークンはこの起動中だけ使う一時値です。画面に表示した値をiPhoneショートカットの認証ヘッダーに設定します。

```bash
export KIHACHI_JARVIS_TAILSCALE_USER="あなたのTailscaleログイン名"
export KIHACHI_JARVIS_SHORTCUT_TOKEN="$(openssl rand -hex 32)"
printf 'Shortcut token: %s\n' "$KIHACHI_JARVIS_SHORTCUT_TOKEN"
uv run python -m kihachi_mcp.studio --tailscale-shortcut
```

別のターミナルで、専用ポートだけをServeします。表示された`https://...ts.net`のホスト名をショートカットに使います。

```bash
tailscale serve 8766
```

Tailscale Serveの転送先が`127.0.0.1:8766`であることを確認してください。8765のStudio本体はServeしないでください。テスト後はServeのターミナルでCtrl+Cを押し、Studio側もCtrl+Cで停止します。

## iPhoneショートカット

ショートカットアプリで新規ショートカットを作り、次のアクションを順番に追加します。

1. **テキストを音声入力**（言語は日本語）
2. **URLの内容を取得**
   - URL: `https://<MacのTailscaleホスト名>.ts.net/api/jarvis/shortcut`
   - 方法: `POST`
   - 本文: `JSON`
   - JSON項目: `utterance` に手順1の音声入力結果
   - ヘッダー: `Content-Type` = `application/json`
   - ヘッダー: `Authorization` = `Bearer <Macで表示されたShortcut token>`
3. **辞書の値を取得**でキー`reply`を取り出す
4. **結果を表示**（または**テキストを読み上げる**）

例えば「今の曲はどうなってる？」と話すと、JARVISの返答を表示できます。認証トークンを含むショートカットは他人に共有しないでください。

## 注意点

- `KIHACHI_JARVIS_TAILSCALE_USER`と一致しないログイン名からの要求は拒否されます。Tailscaleのタグ付きデバイスなど、ユーザーログイン名ヘッダーが付かない要求も拒否されます。
- この実装が受け取るのは音声認識後のテキストです。iOSの音声入力結果はApple側の設定やiOSの状態に依存します。
- JARVISは既存の読み取り・提案処理を使います。Liveへ書き込む操作はこの入口から行いません。
- トークンを更新するときはStudioを停止し、新しいトークンを生成してiPhoneショートカットのヘッダーも更新してください。
