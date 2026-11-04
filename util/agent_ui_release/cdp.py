#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drive the Agent UI page over the DevTools port that launch_isolated.py opens.

The fallback when computer use can't reach something: access was denied, or a
control is hidden under another (a link under the Windows title-bar buttons).
Whatever it does is "driven through DevTools", not "a person could do this";
say so in the report.

    python cdp.py --port 9333 shot OUT.png            # a fresh frame of the page
    python cdp.py --port 9333 click "Install GAIA agent"   # click a button or link by its text
    python cdp.py --port 9333 eval "document.title"   # evaluate JS, print the result
    python cdp.py --port 9333 text                    # the page's visible text
    python cdp.py --port 9333 type "textarea" "Hello" # focus an element, type into it
    python cdp.py --port 9333 press Enter

Standard library only, any OS.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import sys
import urllib.parse
import urllib.request
from pathlib import Path


class Page:
    """One WebSocket connection to the app's main page."""

    def __init__(self, port: int):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/list", timeout=3
            ) as r:
                pages = [
                    p
                    for p in json.loads(r.read().decode("utf-8"))
                    if p.get("type") == "page"
                ]
        except OSError as e:
            sys.exit(
                f"No DevTools on port {port} ({e}). Start the app with launch_isolated.py --cdp."
            )
        page = next(
            (p for p in pages if "index.html" in p.get("url", "")),
            pages[0] if pages else None,
        )
        if not page:
            sys.exit(f"No page on DevTools port {port}.")
        u = urllib.parse.urlparse(page["webSocketDebuggerUrl"])
        self.sock = socket.create_connection((u.hostname, u.port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.sock.recv(1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            sys.exit(f"DevTools refused the connection: {head[:120]!r}")
        self.next_id = 0

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                sys.exit("DevTools closed the connection.")
            buf += chunk
        return buf

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        msg = json.dumps(
            {"id": self.next_id, "method": method, "params": params or {}}
        ).encode()
        mask = os.urandom(4)
        if len(msg) < 126:
            hdr = bytes([0x81, 0x80 | len(msg)])
        elif len(msg) < 65536:
            hdr = bytes([0x81, 0xFE]) + struct.pack(">H", len(msg))
        else:
            hdr = bytes([0x81, 0xFF]) + struct.pack(">Q", len(msg))
        self.sock.sendall(
            hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(msg))
        )
        while True:
            payload = b""
            while True:  # one message, possibly fragmented
                b0, b1 = self._read(2)
                n = b1 & 0x7F
                if n == 126:
                    n = struct.unpack(">H", self._read(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", self._read(8))[0]
                payload += self._read(n)
                if b0 & 0x80:
                    break
            reply = json.loads(payload)
            if reply.get("id") == self.next_id:
                if "error" in reply:
                    sys.exit(f"{method} failed: {reply['error']}")
                return reply["result"]

    def eval(self, expr: str):
        res = self.call(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
        )
        if "exceptionDetails" in res:
            sys.exit(f"JS error: {res['exceptionDetails'].get('text')}")
        return res["result"].get("value")

    def close(self) -> None:
        self.sock.close()


def shot(port: int, out: Path) -> tuple[int, int]:
    page = Page(port)
    png = base64.b64decode(
        page.call("Page.captureScreenshot", {"format": "png"})["data"]
    )
    page.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    return struct.unpack(">II", png[16:24])


KEYS = {
    "Enter": ("Enter", "Enter", 13),
    "Escape": ("Escape", "Escape", 27),
    "Tab": ("Tab", "Tab", 9),
}

CLICK_JS = """(() => {
  const want = %s.toLowerCase();
  const els = [...document.querySelectorAll('button, a, [role=button], [role=menuitem], [role=tab], label')];
  const hit = els.filter(e => (e.innerText || e.getAttribute('aria-label') || '').trim().toLowerCase() === want)
           .concat(els.filter(e => (e.innerText || e.getAttribute('aria-label') || '').toLowerCase().includes(want)));
  if (!hit.length) return null;
  hit[0].click();
  return (hit[0].innerText || hit[0].getAttribute('aria-label')).trim();
})()"""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", type=int, required=True, help="DevTools port (--cdp)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("shot").add_argument("out", type=Path)
    sub.add_parser("click").add_argument("text")
    sub.add_parser("eval").add_argument("expr")
    sub.add_parser("text")
    sp = sub.add_parser("type", help="Focus SELECTOR, then insert TEXT as typed input")
    sp.add_argument("selector")
    sp.add_argument("text")
    sub.add_parser("press", help="Press a key in the focused element").add_argument(
        "key", choices=sorted(KEYS)
    )
    a = ap.parse_args()
    if a.cmd == "shot":
        w, h = shot(a.port, a.out)
        print(f"{a.out} ({w}x{h})")
        return
    page = Page(a.port)
    if a.cmd == "click":
        clicked = page.eval(CLICK_JS % json.dumps(a.text))
        if clicked is None:
            sys.exit(f"No button or link reading '{a.text}' on the page.")
        print(f"clicked: {clicked}")
    elif a.cmd == "type":
        found = page.eval(
            f"(() => {{ const e = document.querySelector({json.dumps(a.selector)}); "
            "if (!e) return false; e.focus(); return true; })()"
        )
        if not found:
            sys.exit(f"Nothing matches {a.selector}.")
        # insertText goes through the browser's input path, so React sees it as typing.
        page.call("Input.insertText", {"text": a.text})
        print(f"typed {len(a.text)} chars")
    elif a.cmd == "press":
        name, code, vk = KEYS[a.key]
        for kind in ("keyDown", "keyUp"):
            page.call(
                "Input.dispatchKeyEvent",
                {"type": kind, "key": name, "code": code, "windowsVirtualKeyCode": vk},
            )
        print(f"pressed {a.key}")
    elif a.cmd == "eval":
        print(json.dumps(page.eval(a.expr), ensure_ascii=False, indent=1))
    else:
        print(page.eval("document.body.innerText"))
    page.close()


if __name__ == "__main__":
    main()
