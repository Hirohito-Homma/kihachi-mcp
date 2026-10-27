# Manus から KIHACHI Ableton MCP を登録する

公式のカスタム MCP 説明（2026-10-02 に確認）: <https://manus.im/docs/integrations/custom-mcp>

そこに書かれている登録項目は次の3つです。

- Server name: 分かりやすい名前
- Server URL: HTTPS のエンドポイント
- Authentication: API キー、Bearer トークン、またはそのサーバーが求める資格情報

画面のドロップダウンが "Streamable HTTP" というラベルかは、そのページには書かれていません。この Gateway の実体は MCP SDK / FastMCP 4.0.3 の Streamable HTTP（`streamable-http`）で、パスは `/mcp` です。SSE 用の入口は用意していません。フォームに transport の選択がある場合は HTTP（Streamable HTTP）を選び、SSE は選ばないでください。

## 入れる値

```text
Name:
KIHACHI Ableton MCP

Transport:
Streamable HTTP
（フォームの表記が HTTP だけのときはこちら。SSE ではない）

Server URL:
https://<PUBLIC_HOST>/mcp

Authentication:
Bearer トークン
Authorization: Bearer <KIHACHI_MCP_API_TOKEN>
```

`<PUBLIC_HOST>` はトンネルかリバースプロキシの HTTPS 名です。`http://127.0.0.1:8787/mcp` は同じ Mac からの確認用で、Manus のクラウドからは届きません。

トークンは `openssl rand -hex 32` で作り、Manus の認証欄と、Gateway を起動したシェルの `KIHACHI_MCP_API_TOKEN` だけに置きます。チャットや Git には貼らないでください。

## 起動してから登録する

1. Live を起動し、コントロールサーフェスで `AbletonGPT_MCP` を有効にします。Remote Script は `127.0.0.1:9877` のままです。
2. 別ターミナルで Gateway を起動します。

```bash
export KIHACHI_MCP_API_TOKEN="$(openssl rand -hex 32)"
uv run kihachi serve --transport streamable-http
```

3. 同じ Mac で `curl -sS http://127.0.0.1:8787/health` が `status: ok` になることを確認します。
4. トンネルを `http://127.0.0.1:8787` へ向けます。9877 と 8765 は向けません。cloudflared 2026.9.3 では `--http-host-header 127.0.0.1:8787` が必要です。手順は [MCP_GATEWAY.md](MCP_GATEWAY.md) です。
5. Manus に上の Name / URL / Bearer を登録します。Save のときに Manus が `tools/list` を取りにきます。401 のときはトークンの不一致、403 のときは `KIHACHI_MCP_ALLOWED_HOSTS` です。
6. 最初の確認は `ableton_ping` です。`connected: true` なら Remote Script が応答しています。`live_version` と `project` が null なのは未実装ではなく、Remote Script が返さないためです。
7. テンポを変えるときは、希望の BPM を人が確認してから `set_tempo` を `confirm=true` で 1 回だけ使います。確認なしの呼び出しは Live を変えません。
8. 曲を作るときは、先に `produce_ableton_song` を `dry_run=true` で呼び、計画を見てから `confirm=true` を 1 回だけ付けます。`dry_run` は Live を変えません。手順は [ABLETON_SONG_PRODUCTION.md](ABLETON_SONG_PRODUCTION.md) です。

## 届かないとき

| 現象 | 見る場所 |
| --- | --- |
| health が開かない | Gateway が 8787 で起動しているか。別プロセスがポートを使っていないか |
| `/mcp` が 401 | Bearer が起動中の `KIHACHI_MCP_API_TOKEN` と一致しているか |
| トンネル経由だけ 403 | 公開ホスト名を `KIHACHI_MCP_ALLOWED_HOSTS` に足して Gateway を起動し直す |
| `ableton_connected: false` | Live と AbletonGPT_MCP コントロールサーフェス。Gateway はそのまま動きます |
| テンポが変わらない | `confirm=true` が付いているか。BPM は 20〜999 か |
