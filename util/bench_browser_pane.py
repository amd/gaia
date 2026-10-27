#!/usr/bin/env python3
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Time a browser_click and its follow-up snapshot, headless and in the pane.

Both drivers click the same element of the fixture sign-up form, served from
``tests/fixtures/browser_pane/site/``, and each timing covers the click and
the snapshot it returns, which is what ``browser_click`` costs the agent. Runs
alternate between the two so a busy machine slows both alike.

The pane is the running GAIA desktop's: start one with
``scripts/qa-pane.mjs launch`` and pass its discovery file and the chat
session it shows (``qa-pane.mjs session`` prints both).

    python util/bench_browser_pane.py --op click --runs 20 \\
        --pane-discovery <file> --pane-session <id>

The rule: the pane's median at most 1.5 times headless, and no click over 2 s.
Exits 1 when the rule fails.
"""

from __future__ import annotations

import argparse
import functools
import statistics
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Dict, List, Tuple

REPO = Path(__file__).resolve().parents[1]
SITE = REPO / "tests" / "fixtures" / "browser_pane" / "site"
TARGET = ("checkbox", "Send me news")
RATIO_LIMIT = 1.5
CLICK_LIMIT_S = 2.0


def serve_site() -> Tuple[ThreadingHTTPServer, str]:
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=str(SITE))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/form.html"


def ref_of(snap: Dict) -> str:
    role, name = TARGET
    for el in snap.get("elements") or []:
        if el.get("role") == role and el.get("name") == name:
            return el["ref"]
    raise SystemExit(f"The form has no {role} named {name!r}; is {SITE} intact?")


def headless(url: str):
    from gaia.browser import driver as browser_driver

    if not browser_driver.installed():
        raise SystemExit(
            "Playwright is not installed here, so there is no headless baseline. "
            "Install it with: pip install 'amd-gaia[browser]' && python -m "
            "playwright install chromium"
        )
    drv = browser_driver.PlaywrightDriver(headless=True, allow_navigation=lambda _u: True)
    drv.start()
    return drv, drv.goto(url)


def pane(url: str, discovery: str, session: str):
    import os

    from gaia.browser._pane_bridge import DISCOVERY_ENV, PaneBridge, discover
    from gaia.browser.pane_driver import PaneDriver

    os.environ[DISCOVERY_ENV] = discovery
    disc = discover()
    if disc is None:
        raise SystemExit(f"No running GAIA desktop wrote {discovery}.")
    bridge = PaneBridge(disc)
    bridge.hello()
    if bridge.attach(session)["status"] != "attached":
        raise SystemExit(f"Chat {session} is not the one GAIA desktop shows.")
    drv = PaneDriver(bridge, session)
    return drv, drv.goto(url)


def clicker(drv, snap) -> Callable[[], float]:
    ref = [ref_of(snap)]

    def one() -> float:
        start = time.perf_counter()
        after = drv.click(ref[0])
        took = time.perf_counter() - start
        ref[0] = ref_of(after)
        return took

    return one


def summary(name: str, times: List[float]) -> str:
    ms = sorted(t * 1000 for t in times)
    p90 = ms[max(0, int(len(ms) * 0.9) - 1)]
    return (
        f"{name:9} median {statistics.median(ms):7.0f} ms   p90 {p90:7.0f} ms   "
        f"max {ms[-1]:7.0f} ms   n={len(ms)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--op", choices=["click"], default="click")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--pane-discovery", required=True)
    parser.add_argument("--pane-session", required=True)
    args = parser.parse_args()

    server, url = serve_site()
    sides: Dict[str, Tuple[object, Callable[[], float]]] = {}
    try:
        h_drv, h_snap = headless(url)
        sides["headless"] = (h_drv, clicker(h_drv, h_snap))
        p_drv, p_snap = pane(url, args.pane_discovery, args.pane_session)
        sides["pane"] = (p_drv, clicker(p_drv, p_snap))
        for side in sides.values():
            side[1]()  # warm-up, not counted
        times: Dict[str, List[float]] = {name: [] for name in sides}
        for _ in range(args.runs):
            for name, (_drv, one) in sides.items():
                times[name].append(one())
    finally:
        for drv, _one in sides.values():
            drv.close()
        server.shutdown()

    print(f"browser_click + snapshot on {url}, {args.runs} runs each, interleaved")
    for name, ts in times.items():
        print(summary(name, ts))
    ratio = statistics.median(times["pane"]) / statistics.median(times["headless"])
    slowest = max(times["pane"])
    ok = ratio <= RATIO_LIMIT and slowest <= CLICK_LIMIT_S
    print(
        f"pane / headless median: {ratio:.2f} (limit {RATIO_LIMIT}); "
        f"slowest pane click {slowest * 1000:.0f} ms (limit {CLICK_LIMIT_S * 1000:.0f} ms)"
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
