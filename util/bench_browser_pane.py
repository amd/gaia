#!/usr/bin/env python3
# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Time browser-pane ops: a click against headless, or starting a dev server.

``--op click`` times a browser_click and its follow-up snapshot, headless and
in the pane.

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

``--op devserver`` starts the fixture dev server
(``tests/fixtures/browser_pane/devserver``) through the pane's supervisor and
times it to its port answering and to the pane's page load, against the same
server started directly from here. Runs alternate. The rule: the supervisor's
median overhead beyond the server's own boot at most 500 ms.

Exits 1 when the rule fails.
"""

from __future__ import annotations

import argparse
import functools
import socket
import statistics
import subprocess
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
DEVSERVER_APP = REPO / "tests" / "fixtures" / "browser_pane" / "devserver"
OVERHEAD_LIMIT_S = 0.5


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


def connect(discovery: str, session: str):
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
    return PaneDriver(bridge, session)


def pane(url: str, discovery: str, session: str):
    drv = connect(discovery, session)
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


def port_answers(port: int) -> bool:
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            continue
    return False


def wait_for_port(port: int, timeout_s: float = 20.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while not port_answers(port):
        if time.perf_counter() > deadline:
            raise SystemExit(f"Nothing answered on port {port} within {timeout_s} s.")
        time.sleep(0.01)


def bench_devserver(args) -> int:
    from gaia.browser.dev_servers import spec_named

    spec = spec_named(DEVSERVER_APP, "web", allowed=lambda _p: True)
    if port_answers(spec.port):
        raise SystemExit(f"Port {spec.port} is already taken; stop whatever holds it.")
    drv = connect(args.pane_discovery, args.pane_session)
    direct: List[float] = []
    ensure: List[float] = []
    load: List[float] = []

    def run_direct() -> float:
        start = time.perf_counter()
        proc = subprocess.Popen(
            list(spec.argv), cwd=spec.cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            wait_for_port(spec.port)
            return time.perf_counter() - start
        finally:
            proc.terminate()
            proc.wait(10)

    def run_supervised() -> Tuple[float, float]:
        start = time.perf_counter()
        res = drv.dev_server_ensure(spec.key, spec.to_wire())
        ready = time.perf_counter() - start
        if res["reused"]:
            raise SystemExit("The supervisor reused a running server; stop it first.")
        drv.goto(spec.url)
        loaded = time.perf_counter() - start
        drv.dev_server_stop(spec.key)
        return ready, loaded

    run_direct()  # warm-up, not counted
    run_supervised()
    for _ in range(args.runs):
        direct.append(run_direct())
        ready, loaded = run_supervised()
        ensure.append(ready)
        load.append(loaded)
        while port_answers(spec.port):
            time.sleep(0.01)

    print(f"fixture dev server on port {spec.port}, {args.runs} runs each, interleaved")
    print(summary("direct", direct))
    print(summary("ensure", ensure))
    print(summary("to load", load))
    overhead = statistics.median(ensure) - statistics.median(direct)
    ok = overhead <= OVERHEAD_LIMIT_S
    print(
        f"supervisor overhead beyond the server's own boot: {overhead * 1000:.0f} ms "
        f"(limit {OVERHEAD_LIMIT_S * 1000:.0f} ms)"
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--op", choices=["click", "devserver"], default="click")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--pane-discovery", required=True)
    parser.add_argument("--pane-session", required=True)
    args = parser.parse_args()
    if args.op == "devserver":
        return bench_devserver(args)

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
