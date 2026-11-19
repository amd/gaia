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
    /resilience/<page>  → a fixed 429/503 (STATUS_ROUTES; bodies under
                          tests/fixtures/gaia/tiers_resilience/http/)
    /tiers_skills_web/... → tests/fixtures/gaia/tiers_skills_web/...
                          (skills/web/tool-selection tier fixtures)

CI eval runs bind **port 8765** — scenarios name http://127.0.0.1:8765 —
with GAIA_HUB_URL=http://127.0.0.1:8765/fixture_hub:

    python tests/fixtures/gaia/prepare_fixture_hub.py --skills-root <agent skills root>
    python tests/fixtures/gaia/serve_fixtures.py --port 8765

``util/run_eval_lane.py`` and unit tests keep the default ``--port 0``
(ephemeral, printed as ``SERVING <url>``) so two runs never collide; the lane
runner passes the real port on as ``GAIA_EVAL_FIXTURE_PORT`` and the runner
rewrites the scenarios to it (``gaia.eval.fixture_env``). ``--hub-dir`` serves
a run's own prepared hub instead of the checkout's shared ``_prepared``.
Never 4001/4200/8141/13305.

``--dir`` switches to PLAIN single-directory serving (no routing) — used by
tests that serve one prepared hub directly.
"""

from __future__ import annotations

import argparse
import io
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES_ROOT = Path(__file__).resolve().parent
DEFAULT_HUB_DIR = FIXTURES_ROOT / "fixture_hub" / "_prepared"


def routes(hub_dir: Path = DEFAULT_HUB_DIR) -> tuple[tuple[str, Path], ...]:
    """(url-prefix, directory) pairs — first match wins; "" is the web root."""
    return (
        ("/rss", FIXTURES_ROOT / "rss"),
        ("/fixture_hub", hub_dir),
        ("/capture", FIXTURES_ROOT / "capture"),
        ("/tiers_skills_web", FIXTURES_ROOT / "tiers_skills_web"),
        ("", FIXTURES_ROOT / "web"),
    )


ROUTES = routes()

_HTTP_ERRORS = FIXTURES_ROOT / "tiers_resilience" / "http"

#: Exact paths answered with an error status (resilience scenarios):
#: path -> (status, extra headers, body file).
STATUS_ROUTES: dict[str, tuple[int, dict[str, str], Path]] = {
    "/resilience/rate_limited.html": (
        429,
        {"Retry-After": "3600"},
        _HTTP_ERRORS / "rate_limited.html",
    ),
    "/resilience/summit_tent.html": (
        503,
        {"Retry-After": "120"},
        _HTTP_ERRORS / "unavailable.html",
    ),
    "/resilience/status_board.html": (
        503,
        {"Retry-After": "600"},
        _HTTP_ERRORS / "status_board_injected.html",
    ),
}


class _QuietHandler(SimpleHTTPRequestHandler):
    """Per-request stderr chatter off; errors still surface via status codes."""

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass


class _RoutedHandler(_QuietHandler):
    """Map the contract's root-relative URL layout onto the fixture dirs."""

    def __init__(self, *args, routes=ROUTES, **kwargs):
        self.routes = routes
        super().__init__(*args, **kwargs)

    def send_head(self):
        clean = self.path.split("?", 1)[0].split("#", 1)[0]
        route = STATUS_ROUTES.get(clean)
        if route is None:
            return super().send_head()
        status, headers, body_file = route
        body = body_file.read_bytes()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        return io.BytesIO(body)

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        for prefix, directory in self.routes:
            if not prefix or clean == prefix or clean.startswith(prefix + "/"):
                self.directory = str(directory)
                remainder = clean[len(prefix) :] or "/"
                return super().translate_path(remainder)
        return super().translate_path(clean)  # unreachable: "" always matches


def make_server(
    port: int, directory: Path | None = None, hub_dir: Path | None = None
) -> ThreadingHTTPServer:
    """Build (but do not start) the server; caller owns serve/shutdown.

    ``directory=None`` (the default) serves the routed fixture layout above,
    with ``/fixture_hub`` from *hub_dir* when given; a path serves that single
    directory plainly.
    """
    if directory is None:
        handler = partial(
            _RoutedHandler,
            directory=str(FIXTURES_ROOT / "web"),
            routes=routes(hub_dir) if hub_dir is not None else ROUTES,
        )
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
            "Port to bind on 127.0.0.1. Default 0 = ephemeral (printed). CI "
            "eval runs pass --port 8765 (the base URL scenarios name)."
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
    parser.add_argument(
        "--hub-dir",
        type=Path,
        default=None,
        help="Serve /fixture_hub from this prepared hub (prepare_fixture_hub.py "
        "--out) instead of fixture_hub/_prepared.",
    )
    args = parser.parse_args(argv)

    directory = args.dir.resolve() if args.dir is not None else None
    hub_dir = args.hub_dir.resolve() if args.hub_dir is not None else None
    if directory is None and not (hub_dir or DEFAULT_HUB_DIR).is_dir():
        print(
            f"WARNING: {hub_dir or DEFAULT_HUB_DIR} does not exist — /fixture_hub/* "
            "will 404. Run prepare_fixture_hub.py first if this run needs the hub.",
            file=sys.stderr,
        )

    server = make_server(args.port, directory, hub_dir)
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
