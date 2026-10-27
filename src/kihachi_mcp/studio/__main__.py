"""Launch the local studio UI and open it in the default browser."""

import argparse
import os
import re
import socket
import sys
import time
import webbrowser
from threading import Thread

from kihachi_mcp.studio.app import HOST, JARVIS_SHORTCUT_PORT, PORT, StudioApp


def main(argv: list[str] | None = None) -> int:
    """Start the loopback UI. Cursor and Codex are not required."""
    parser = argparse.ArgumentParser(description="KIHACHI 制作画面を起動します")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--tailscale-shortcut",
        action="store_true",
        help="JARVIS専用iPhone Shortcut APIをTailscale Serve経由で有効にする",
    )
    args = parser.parse_args(argv)
    shortcut_user = None
    shortcut_token = None
    if args.tailscale_shortcut:
        shortcut_user = os.environ.get("KIHACHI_JARVIS_TAILSCALE_USER", "").strip()
        shortcut_token = os.environ.get("KIHACHI_JARVIS_SHORTCUT_TOKEN", "").strip()
        if not shortcut_user or "@" not in shortcut_user:
            parser.error("--tailscale-shortcut には KIHACHI_JARVIS_TAILSCALE_USER (Tailscaleログイン名) が必要です")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", shortcut_token):
            parser.error("--tailscale-shortcut には openssl rand -hex 32 で生成した64桁の KIHACHI_JARVIS_SHORTCUT_TOKEN が必要です")
        if args.host != HOST:
            parser.error("Tailscale Shortcut利用時は --host 127.0.0.1 のみ指定できます")
        if args.port == JARVIS_SHORTCUT_PORT:
            parser.error(f"Studioポートに専用ポート {JARVIS_SHORTCUT_PORT} は指定できません")
    if _port_taken(args.host, args.port):
        print(
            f"ポート {args.port} は使用中です。既に起動している画面を開くか、"
            "先にそのプロセスを終了してください。",
            file=sys.stderr,
        )
        if not args.no_browser:
            webbrowser.open(f"http://{args.host}:{args.port}/")
        return 1
    if args.tailscale_shortcut and _port_taken(HOST, JARVIS_SHORTCUT_PORT):
        print(
            f"JARVIS専用ポート {JARVIS_SHORTCUT_PORT} は使用中です。"
            "他のプロセスが停止してから起動してください。",
            file=sys.stderr,
        )
        return 1
    app = StudioApp()
    url = f"http://{args.host}:{args.port}/"

    startup_errors: list[OSError] = []

    def serve() -> None:
        try:
            app.serve_forever(
                args.host,
                args.port,
                tailscale_shortcut_user=shortcut_user,
                tailscale_shortcut_token=shortcut_token,
            )
        except OSError as exc:
            startup_errors.append(exc)

    thread = Thread(
        target=serve,
        daemon=True,
    )
    thread.start()
    if not _wait_for_port(args.host, args.port, thread):
        if startup_errors:
            print(
                f"Studioサーバーを起動できませんでした: {startup_errors[0]}",
                file=sys.stderr,
            )
        else:
            print("Studioサーバーの起動を確認できませんでした。", file=sys.stderr)
        return 1
    print(f"KIHACHI 制作画面: {url}")
    if shortcut_user:
        print(f"JARVIS専用のTailscale中継先: http://127.0.0.1:{JARVIS_SHORTCUT_PORT}")
        print("Tailscale Serveはこの専用ポートだけを公開してください。Studio本体ポートは公開しません。")
    print("終了するには Ctrl+C を押してください。")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n停止します")
    return 0


def _port_taken(host: str, port: int) -> bool:
    probe = socket.socket()
    try:
        probe.settimeout(0.3)
        return probe.connect_ex((host, port)) == 0
    finally:
        probe.close()


def _wait_for_port(
    host: str, port: int, thread: Thread, timeout: float = 5.0
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline and thread.is_alive():
        if _port_taken(host, port):
            return True
        time.sleep(0.05)
    return _port_taken(host, port)


if __name__ == "__main__":
    raise SystemExit(main())
