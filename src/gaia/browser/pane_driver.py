# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The browser driver over GAIA desktop's browser pane.

Python keeps the browsing semantics (the snapshot script, refs, the frame
merge, rendering); the desktop app's main process runs the browser (tabs,
CDP, the console and network buffers). The agent reads the tab the user is
looking at, so "the page open in the browser pane" means what it says.

Every op that reads or loads a page carries ``allowedOrigins``. Loopback
needs no entry. Main refuses to read a page on any other origin, and stops a
navigation that would land on one during the op, so a page that wanders off
while the agent reads it is never handed to the model.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Literal, Optional, Sequence, Tuple

from gaia.browser._pane_bridge import PaneBridge, is_loopback_origin, origin_of
from gaia.browser.errors import BrowserError, PageOffScope, PaneCannotActYet
from gaia.browser.snapshot import (
    _FIND_JS,
    _SNAPSHOT_JS,
    in_frame_ref,
    merge_frames,
    ref_selector,
    snapshot_args,
)

LogKind = Literal["console", "network"]

#: Most log lines one call returns; the newest are kept.
MAX_LOG_LINES = 50


@dataclass(frozen=True)
class Shot:
    png: bytes
    width: int
    height: int
    url: str


class PaneDriver:
    """:class:`~gaia.browser.base.BrowserDriver` over the desktop browser pane."""

    where = "(in the browser pane)"

    def __init__(self, bridge: PaneBridge, session_id: str) -> None:
        self._bridge = bridge
        self._session = session_id
        self._generation = 0
        self._frames_by_ref: Dict[str, Any] = {}
        self._cursors: Dict[str, int] = {"console": 0, "network": 0}
        self._allowed: Tuple[str, ...] = ()

    @property
    def started(self) -> bool:
        # The pane lives as long as the app does; nothing here can die.
        return True

    def close(self) -> None:
        """The tabs belong to the user, so there is nothing to close."""

    @contextmanager
    def scoped(self, origins: Sequence[str]) -> Iterator[None]:
        """Let the ops inside read pages on ``origins`` as well as loopback."""
        before = self._allowed
        self._allowed = tuple(origins)
        try:
            yield
        finally:
            self._allowed = before

    # ----------------------------------------------------------------- reading

    def goto(self, url: str) -> Dict[str, Any]:
        """Load ``url`` in the tab the agent opened, or in a new tab.

        A tab showing the user's own page is never navigated away: the
        agent opens its own tab beside it instead.
        """
        listing = self._call("tabs", {"op": "list"})
        active = next(t for t in listing["tabs"] if t["id"] == listing["activeTabId"])
        if active["url"] is None or active["openedBy"] == self._session:
            self._call("navigate", {"tabId": active["id"], "to": url, **self._scope()})
        else:
            self._call(
                "tabs",
                {"op": "open", "url": url, **self._scope()},
            )
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        self._generation += 1
        gen = self._generation
        res = self._call(
            "evaluate",
            {
                "tabId": None,
                "script": _SNAPSHOT_JS,
                "args": snapshot_args(gen),
                "frames": "all",
                **self._scope(),
            },
        )
        main, *subframes = res["frames"]
        if not main["ok"]:
            raise BrowserError(
                f"Could not read the page in the browser pane: {main['error']}"
            )
        readable = [
            (f["frameId"], f["value"])
            for f in subframes
            if f["ok"] and isinstance(f.get("value"), dict)
        ]
        snap, self._frames_by_ref = merge_frames(main["value"], readable, gen)
        self._check_scope(snap.get("url") or "")
        return snap

    def back(self) -> Dict[str, Any]:
        self._call("navigate", {"tabId": None, "to": "back", **self._scope()})
        return self.snapshot()

    def find(self, query: str, context: int = 160) -> Dict[str, Any]:
        res = self._call(
            "evaluate",
            {
                "tabId": None,
                "script": _FIND_JS,
                "args": [query, context],
                "frames": "main",
                **self._scope(),
            },
        )
        main = res["frames"][0]
        if not main["ok"]:
            raise BrowserError(
                f"Could not search the page in the browser pane: {main['error']}"
            )
        return main["value"]

    def current_url(self) -> str:
        return self._call("origin", {"tabId": None})["url"] or ""

    def current_origin(self) -> Optional[str]:
        """Live from the pane: the user may have browsed since the last call."""
        return self._call("origin", {"tabId": None})["origin"]

    def origin_has_cookies(self, origin: str) -> bool:
        res = self._call("origin", {"tabId": None})
        return bool(res["hasCookies"]) and res["origin"] == origin

    def screenshot(self, ref: Optional[str] = None, *, max_width: int = 1280) -> Shot:
        """The visible part of the page, or one element of it."""
        clip = None
        if ref:
            ref_selector(ref)
            if ref not in self._frames_by_ref and not ref.startswith(
                f"g{self._generation}e"
            ):
                raise BrowserError(
                    f"Element '{ref}' is not on the page any more. Call "
                    "browser_snapshot() to get current refs, then retry."
                )
            clip = {
                "frameId": self._frames_by_ref.get(ref),
                "selector": ref_selector(in_frame_ref(ref)),
            }
        res = self._call(
            "screenshot",
            {"tabId": None, "clip": clip, "maxWidth": max_width, **self._scope()},
        )
        self._check_scope(res["url"])
        return Shot(
            png=base64.b64decode(res["png"]),
            width=int(res["width"]),
            height=int(res["height"]),
            url=res["url"],
        )

    def logs(
        self,
        kind: LogKind,
        *,
        errors_only: bool,
        pattern: Optional[str],
        new_only: bool,
        limit: int = MAX_LOG_LINES,
    ) -> Dict[str, Any]:
        """Console or network entries for the page's tab, newest last.

        ``new_only`` returns only what arrived since the previous call for
        the same ``kind``; the cursor moves either way.
        """
        res = self._call(
            "logs",
            {
                "tabId": None,
                "kind": kind,
                "afterSeq": self._cursors[kind] if new_only else 0,
                "errorsOnly": errors_only,
                "pattern": pattern or None,
                "limit": limit,
                **self._scope(),
            },
        )
        self._cursors[kind] = max(self._cursors[kind], int(res["lastSeq"]))
        return res

    # --------------------------------------------------- not in the pane (yet)

    def click(self, ref: str) -> Dict[str, Any]:
        raise PaneCannotActYet("browser_click")

    def type_text(
        self, ref: str, text: str, *, press_enter: bool = False
    ) -> Dict[str, Any]:
        raise PaneCannotActYet("browser_type")

    def storage_state(self) -> Dict[str, Any]:
        raise PaneCannotActYet("browser_login")

    def wait_for_login(self, url: str, *, timeout_s: float = 300.0) -> Dict[str, Any]:
        raise PaneCannotActYet("browser_login")

    # --------------------------------------------------------------- internals

    def _scope(self) -> Dict[str, List[str]]:
        return {"allowedOrigins": list(self._allowed)}

    def _check_scope(self, url: str) -> None:
        """Main checks too; this covers a page that moved while it was read."""
        origin = origin_of(url)
        if origin and not is_loopback_origin(origin) and origin not in self._allowed:
            raise PageOffScope(origin)

    def _call(self, op: str, body: Dict[str, Any]) -> Any:
        # Main checks it on every op: the user may have switched chats since attach.
        return self._bridge.call(op, {"sessionId": self._session, **body})


__all__ = ["LogKind", "MAX_LOG_LINES", "PaneDriver", "Shot"]
