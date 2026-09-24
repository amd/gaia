# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Serve the gaia eval fixtures over HTTP (stdlib only, deterministic content).

Default mode serves the ROUTED layout the scenario corpus assumes
(eval/scenarios/GAIA_FIXTURE_VALUES.md — scenario URLs are root-relative):

    /<page>.html        → tests/fixtures/gaia/web/<page>.html
    /rss/feed.xml       → tests/fixtures/gaia/rss/feed.xml
    /fixture_hub/...    → tests/fixtures/gaia/fixture_hub/_prepared/...
                          (built per run by prepare_fixture_hub.py)
    /capture/...        → tests/fixtures/gaia/capture/...
                          (raw SKILL.md fixtures for the capture scenarios)

Eval runs bind **port 8765** — scenarios hardcode http://127.0.0.1:8765 —
with GAIA_HUB_URL=http://127.0.0.1:8765/fixture_hub:

    python tests/fixtures/gaia/prepare_fixture_hub.py --skills-root <agent skills root>
    python tests/fixtures/gaia/serve_fixtures.py --port 8765

Unit tests keep the default ``--port 0`` (ephemeral, printed as
``SERVING <url>``) so they never collide. Never 4001/4200/8141/13305.

``--dir`` switches to PLAIN single-directory serving (no routing) — used by
tests that serve one prepared hub directly.
"""

from __future__ import annotations

import argparse
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parent

#: (url-prefix, directory) — first match wins; "" is the web-root catch-all.
ROUTES: tuple[tuple[str, Path], ...] = (
    ("/rss", FIXTURES_ROOT / "rss"),
    ("/fixture_hub", FIXTURES_ROOT / "fixture_hub" / "_prepared"),
    ("/capture", FIXTURES_ROOT / "capture"),
    ("", FIXTURES_ROOT / "web"),
)


class _QuietHandler(SimpleHTTPRequestHandler):
    """Per-request stderr chatter off; errors still surface via status codes."""

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass


class _RoutedHandler(_QuietHandler):
    """Map the contract's root-relative URL layout onto the fixture dirs."""

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        for prefix, directory in ROUTES:
            if not prefix or clean == prefix or clean.startswith(prefix + "/"):
                self.directory = str(directory)
                remainder = clean[len(prefix) :] or "/"
                return super().translate_path(remainder)
        return super().translate_path(clean)  # unreachable: "" always matches


def make_server(port: int, directory: Path | None = None) -> ThreadingHTTPServer:
    """Build (but do not start) the server; caller owns serve/shutdown.

    ``directory=None`` (the default) serves the routed fixture layout above;
    a path serves that single directory plainly.
    """
    if directory is None:
        handler = partial(_RoutedHandler, directory=str(FIXTURES_ROOT / "web"))
    else:
        if not directory.is_dir():
            raise NotADirectoryError(
                f"Fixture directory does not exist: {directory}. Pass --dir with "
                "a real path, or omit it for the routed fixture layout."
            )
        handler = partial(_QuietHandler, directory=str(directory))
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help=(
            "Port to bind on 127.0.0.1. Default 0 = ephemeral (printed). Eval "
            "runs pass --port 8765 (the base URL scenarios hardcode)."
        ),
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help=(
            "Serve ONE directory plainly instead of the routed fixture layout "
            "(web at /, rss at /rss, prepared hub at /fixture_hub)."
        ),
    )
    args = parser.parse_args(argv)

    directory = args.dir.resolve() if args.dir is not None else None
    if directory is None and not (FIXTURES_ROOT / "fixture_hub" / "_prepared").is_dir():
        print(
            "WARNING: fixture_hub/_prepared does not exist — /fixture_hub/* will "
            "404. Run prepare_fixture_hub.py first if this run needs the hub.",
            file=sys.stderr,
        )

    server = make_server(args.port, directory)
    host, port = server.server_address[0], server.server_address[1]
    served = directory if directory is not None else f"routed layout ({FIXTURES_ROOT})"
    print(f"SERVING http://{host}:{port}/ from {served}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
