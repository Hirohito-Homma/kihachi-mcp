"""Launch the local studio UI and open it in the default browser."""

import argparse
import socket
import sys
import time
import webbrowser
from threading import Thread

from kihachi_mcp.studio.app import HOST, PORT, StudioApp


def main(argv: list[str] | None = None) -> int:
    """Start the loopback UI. Cursor and Codex are not required."""
    parser = argparse.ArgumentParser(description="KIHACHI 制作画面を起動します")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    if _port_taken(args.host, args.port):
        print(
            f"ポート {args.port} は使用中です。既に起動している画面を開くか、"
            "先にそのプロセスを終了してください。",
            file=sys.stderr,
        )
        if not args.no_browser:
            webbrowser.open(f"http://{args.host}:{args.port}/")
        return 1
    app = StudioApp()
    url = f"http://{args.host}:{args.port}/"
    thread = Thread(target=app.serve_forever, args=(args.host, args.port), daemon=True)
    thread.start()
    _wait_for_port(args.host, args.port)
    print(f"KIHACHI 制作画面: {url}")
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


def _wait_for_port(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_taken(host, port):
            return
        time.sleep(0.05)


if __name__ == "__main__":
    raise SystemExit(main())
