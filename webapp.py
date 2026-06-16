"""Launch the AutoRCA web portal.

    python webapp.py                # serve on http://127.0.0.1:5000
    python webapp.py --port 8080    # custom port
    python webapp.py --host 0.0.0.0 # expose on the network

The portal is read-only over the same history database the monitor writes, so
you can run it at the same time as `python main.py`.
"""
from __future__ import annotations

import argparse
import socket
import threading
import webbrowser

from autorca.config import load_config
from autorca.web import create_app


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoRCA web portal")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--config", default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    args = parser.parse_args()

    config = load_config(args.config)
    app = create_app(config)

    # If the requested port is taken (common on Windows: 5000 is sometimes used),
    # automatically find the next free one so the portal always comes up.
    port = args.port
    if not _port_is_free(args.host, port):
        for candidate in range(port + 1, port + 20):
            if _port_is_free(args.host, candidate):
                print(f"  Port {port} is busy — using {candidate} instead.")
                port = candidate
                break

    shown_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    url = f"http://{shown_host}:{port}"
    print("\n" + "=" * 52)
    print("  AutoRCA portal is running.")
    print(f"  Open this in your browser:  {url}")
    print("  Keep this window open. Press Ctrl+C to stop.")
    print("=" * 52 + "\n")

    # Auto-open the browser shortly after the server starts listening.
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    # use_reloader=False so the browser only opens once and Ctrl+C is clean.
    app.run(host=args.host, port=port, debug=args.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
