# MCP Gateway

外部の MCP クライアントから、この Mac 上の Ableton Live へ届く入口です。Ableton 自体はネットワークに出しません。

```text
Manus など
    │  HTTPS
    ▼
トンネル / リバースプロキシ
    │  http://127.0.0.1:8787/mcp
    ▼
kihachi-mcp（既存の FastMCP）
    │  TCP 127.0.0.1:9877
    ▼
AbletonGPT Remote Script
    ▼
Ableton Live
```

制作画面は今までどおり `http://127.0.0.1:8765/` です。Gateway はそのポートを使いません。Max for Live の UDP 17771 / 17772 も変えません。

## 起動

stdio（Cursor など、今までの入口）:

```bash
uv run kihachi serve --transport stdio
```

`python -m kihachi_mcp.server` も stdio 固定です。環境変数で HTTP に切り替わりません。

Streamable HTTP（FastMCP 4.0.3 の `streamable-http`。実装は SDK の HTTP transport と同じです）:

```bash
export KIHACHI_MCP_API_TOKEN="$(openssl rand -hex 32)"
uv run kihachi serve --transport streamable-http
```

待受の既定は `127.0.0.1:8787`、パスは `/mcp` です。`KIHACHI_MCP_HOST=0.0.0.0` や LAN のアドレスは起動を拒否します。名前は `.env.example` にあります。値はシェルの環境変数に置き、`.env` を Git に入れないでください。

| 変数 | 既定 | 意味 |
| --- | --- | --- |
| `KIHACHI_MCP_TRANSPORT` | `stdio` | `serve` がフラグ無しのときに使う |
| `KIHACHI_MCP_HOST` | `127.0.0.1` | ループバック以外は拒否 |
| `KIHACHI_MCP_PORT` | `8787` | 8765 でも 9877 でもない |
| `KIHACHI_MCP_PATH` | `/mcp` | `/health` は使えない |
| `KIHACHI_MCP_AUTH_ENABLED` | `true` | HTTP ではトークン必須 |
| `KIHACHI_MCP_API_TOKEN` | 空 | 16文字以上。ログに出ない |
| `KIHACHI_MCP_MAX_BODY_BYTES` | `1048576` | 超えると 413 |
| `KIHACHI_MCP_ABLETON_TIMEOUT_SECONDS` | `5` | 無応答は `timeout`。再送しない |
| `ABLETONGPT_HOST` | `127.0.0.1` | ループバック以外は拒否 |
| `ABLETONGPT_PORT` | `9877` | Remote Script。未設定時は `KIHACHI_ABLETONGPT_PORT` |

健康確認は MCP ではありません。

```bash
curl -sS http://127.0.0.1:8787/health
```

戻りは `{"status":"ok","service":"kihachi-mcp"}` です。トークンも Ableton の状態も入っていません。

## 認証

HTTP の `/mcp` は `Authorization: Bearer <KIHACHI_MCP_API_TOKEN>` が無いと 401 です。`/health` だけは通ります。トークンが空、または 16 文字未満のときは HTTP サーバーを起動しません。

AbletonGPT のセッショントークンは `~/Library/Application Support/AbletonGPT/config.json` から読みます。MCP の Bearer とは別物で、Tool の戻り値とログには出しません。

## Ableton が止まっていても

Gateway は落ちません。`ableton_ping` は `connected: false` と `error: "unavailable"` または `"timeout"` を返します。`server_status` は Gateway が生きていれば `status: "ok"` で、`ableton_connected` だけ false です。

`ableton_ping` / `ableton_status` の `live_version` と `project` は常に null です。Remote Script の `ping` と `get_state` が Live の版と `.als` 名を返さないためです。推測で埋めません。

## テンポ

`set_tempo` は人が確認する操作です。`confirm` を省略した呼び出しは Live に何も送らず、`confirmation_required` を返します。変えるときは、その BPM でよいか確認したあと `confirm=true` を付けて 1 回だけ呼びます。失敗しても自動ではやり直しません。BPM は 20 から 999 です。

## ツールの範囲

同じサーバーに、今までの Brain / Studio の Tool も載っています。HTTP で繋ぐとそれも見えます。Live への既存の適用経路（承認トークン、`[KIHACHI]` トラック以外を変えないこと）はそのままです。

`ableton_tool_catalogue` が同じ表を返します。曲の組み立ては [ABLETON_SONG_PRODUCTION.md](ABLETON_SONG_PRODUCTION.md) です。

| 状態 | 内容 |
| --- | --- |
| SUPPORTED | `ableton_ping`, `ableton_status`, `server_status`, `set_tempo`（確認あり）, `ableton_tool_catalogue`, `ableton_play` / `ableton_stop`（確認あり）, `ableton_list_tracks`, `ableton_create_midi_track`, `ableton_create_audio_track`（どちらも末尾へ追加）, `ableton_create_midi_clip`, `get_midi_clip_notes`（Session MIDI を読み取り。完全読取か切り詰めかを返す。Remote Script が返す `track_index` / `clip_index` / トラック名が要求と食い違う場合や `clip` が無い場合は `FAILED`）, `ableton_add_notes`, `ableton_set_volume`, `ableton_set_pan`, `ableton_mute`, `ableton_solo`, `ableton_list_devices`, `ableton_get_arrangement`, `produce_ableton_song`, `sample_library_status`, `index_sample_library`, `search_samples`, `get_sample`, `find_samples_for_song`, `select_sample`, `place_sample` |
| PARTIAL | Remote Script にはあるが、この曲の経路では呼ばない。クリップ発射、デバイスの追加、プリセットのロード、パラメータ変更、`toggle_cue_at_playhead` |
| NOT IMPLEMENTED | Remote Script にコマンドが無い。`rename_track`（作成時の `name` で足りる）, `delete_track`, `delete_clip`, `overwrite_project`, `replace_device`, `remove_device`, `save_over_existing_project` |

リスクは `safe`（読むだけ）、`requires_confirmation`（Live を変える）、`destructive`（削除・上書き）です。destructive に分類した操作は Tool にしていません。`toggle_cue_at_playhead` は Remote Script 側にあり、キューの削除になり得るため destructive として PARTIAL（非公開）です。

Max for Live デバイスが持つ `set_track_name` などは別経路です。この Gateway は呼びません。

## 外へ出すとき

コードは特定のトンネルに依存しません。どれも転送先は `127.0.0.1:8787` だけにします。9877 を転送しないでください。

1. Cloudflare Tunnel。cloudflared 2026.9.3 では、公開ホスト名をそのまま Origin の Host に渡すと `421` になります。Origin の Host をループバックに固定します。

```bash
cloudflared tunnel --url http://127.0.0.1:8787 --http-host-header 127.0.0.1:8787
```

出た `https://….trycloudflare.com` にパス `/mcp` を足します。この URL はプロセスを止めると消えます。恒久運用は名前付きトンネルとアクセス制限を使います。
2. Tailscale Funnel: この Mac を tailnet に入れ、Funnel の宛先を `http://127.0.0.1:8787` にします。
3. ngrok: `ngrok http 127.0.0.1:8787`。表示された HTTPS URL に `/mcp` を足します。
4. 自前のリバースプロキシ: TLS をプロキシで終端し、`proxy_pass http://127.0.0.1:8787` のようにループバックへ転送します。

トンネルが公開ホスト名を `Host` に残すと、ループバック保護が 403 を返します。そのホスト名だけ `KIHACHI_MCP_ALLOWED_HOSTS` に足してください。`*` は使わないでください。ブラウザ由来の `Origin` で 403 になる場合は、そのオリジンを `KIHACHI_MCP_ALLOWED_ORIGINS` に足します。

Manus への登録欄は [MANUS_MCP.md](MANUS_MCP.md) です。
