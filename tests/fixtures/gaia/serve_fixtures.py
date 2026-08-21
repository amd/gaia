# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Serve the gaia eval fixtures over HTTP (stdlib only, deterministic content).

Eval setup starts this before web / rss-digest / fixture-hub scenarios:

    python tests/fixtures/gaia/serve_fixtures.py --port 8765
    # pages:   http://127.0.0.1:8765/web/price_watch.html
    # rss:     http://127.0.0.1:8765/rss/feed.xml
    # hub:     GAIA_HUB_URL=http://127.0.0.1:8765/fixture_hub

``--port 0`` binds an ephemeral port (printed on stdout as ``SERVING <url>``)
so tests never collide with a fixed port. ``--dir`` defaults to this
directory (the fixtures root); pass ``--dir tests/fixtures/gaia/fixture_hub``
to serve the hub at the URL root instead of under ``/fixture_hub``.
"""

from __future__ import annotations

import argparse
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parent


class _QuietHandler(SimpleHTTPRequestHandler):
    """Per-request stderr chatter off; errors still surface via status codes."""

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass


def make_server(port: int, directory: Path) -> ThreadingHTTPServer:
    """Build (but do not start) the server; caller owns serve/shutdown."""
    if not directory.is_dir():
        raise NotADirectoryError(
            f"Fixture directory does not exist: {directory}. Pass --dir with a "
            "real path (default: tests/fixtures/gaia)."
        )
    handler = partial(_QuietHandler, directory=str(directory))
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind on 127.0.0.1 (default 0 = ephemeral, printed).",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=FIXTURES_ROOT,
        help="Directory to serve (default: the gaia fixtures root).",
    )
    args = parser.parse_args(argv)

    server = make_server(args.port, args.dir.resolve())
    host, port = server.server_address[0], server.server_address[1]
    print(f"SERVING http://{host}:{port}/ from {args.dir.resolve()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
