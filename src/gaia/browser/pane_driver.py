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
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Literal, Optional, Sequence, Tuple

from gaia.browser._pane_bridge import PaneBridge, is_loopback_origin, origin_of
from gaia.browser.errors import (
    BrowserError,
    ElementNotFound,
    PageDialogOpen,
    PageOffScope,
    PaneSignInByHand,
)
from gaia.browser.snapshot import (
    _FIND_JS,
    _SNAPSHOT_JS,
    in_frame_ref,
    is_pick_ref,
    merge_frames,
    ref_selector,
    snapshot_args,
)

# The label the confirmation gate checks before a click on a picked element.
_PICK_LABEL_JS = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return "";
  const label = el.getAttribute("aria-label") || el.innerText || el.value || el.getAttribute("title") || "";
  return String(label).replace(/\\s+/g, " ").trim().slice(0, 120);
}"""

LogKind = Literal["console", "network"]

#: Most log lines one call returns; the newest are kept.
MAX_LOG_LINES = 50

#: The keys the pane presses. Main refuses any other (``KEYS`` in input.cjs).
PANE_KEYS = (
    "Enter",
    "Escape",
    "Tab",
    "Backspace",
    "Space",
    "PageDown",
    "PageUp",
    "Home",
    "End",
    "ArrowLeft",
    "ArrowUp",
    "ArrowRight",
    "ArrowDown",
)

#: Screens browser_viewport offers by name.
VIEWPORT_PRESETS: Dict[str, Dict[str, Any]] = {
    "mobile": {"width": 390, "height": 844, "mobile": True},
    "tablet": {"width": 820, "height": 1180, "mobile": True},
    "desktop": {"width": 1280, "height": 800, "mobile": False},
}


@dataclass(frozen=True)
class ScriptResult:
    """What browser_eval's expression gave: JSON text, or the page's error."""

    ok: bool
    text: str
    truncated: bool
    url: str


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
        self._frame_urls: Dict[Any, str] = {}
        #: True once this turn used the pane, so it may have left a box open; the turn's end releases it.
        self._touched = False
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
        # No active tab in the listing means the user is looking at a private one.
        active = next(
            (t for t in listing["tabs"] if t["id"] == listing["activeTabId"]), None
        )
        if active and (active["url"] is None or active["openedBy"] == self._session):
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
        self._frame_urls = {f["frameId"]: f["url"] for f in subframes}
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

    def pick_label(self, ref: str) -> str:
        """The visible label of the element the user picked as ``ref``, or ""."""
        res = self._call(
            "evaluate",
            {
                "tabId": None,
                "script": _PICK_LABEL_JS,
                "args": ref_selector(ref),
                "frames": "main",
                **self._scope(),
            },
        )
        main = res["frames"][0]
        if not main["ok"]:
            raise BrowserError(
                f"Could not read the picked element in the browser pane: {main['error']}"
            )
        return str(main["value"] or "")

    def frame_origin(self, ref: str) -> Optional[str]:
        """The origin of the frame ``ref`` is in, or None for the main frame or an unknown ref."""
        key = self._frames_by_ref.get(ref)
        return origin_of(self._frame_urls.get(key, "")) if key is not None else None

    def screenshot(self, ref: Optional[str] = None, *, max_width: int = 1280) -> Shot:
        """The visible part of the page, or one element of it."""
        clip = self._target(ref) if ref else None
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

    # ------------------------------------------------------------------ acting

    # ``read=False`` skips the snapshot after an action, so the refs the
    # agent already holds stay valid for the next step of a batch.

    def click(self, ref: str, *, read: bool = True) -> Optional[Dict[str, Any]]:
        self._act({"kind": "click"}, self._target(ref))
        return self.snapshot() if read else None

    def type_text(
        self, ref: str, text: str, *, press_enter: bool = False, read: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Type into a field (replacing what it held), or pick a dropdown option by label."""
        action = {"kind": "fill", "text": text, "pressEnter": press_enter}
        self._act(action, self._target(ref))
        return self.snapshot() if read else None

    def press(
        self, key: str, ref: Optional[str] = None, *, read: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Press one key, on ``ref`` or on whatever has focus."""
        if key not in PANE_KEYS:
            raise BrowserError(
                f"'{key}' is not a key GAIA can press. Use one of: "
                + ", ".join(PANE_KEYS)
                + "."
            )
        self._act({"kind": "press", "key": key}, self._target(ref) if ref else None)
        return self.snapshot() if read else None

    def viewport(
        self, size: Optional[Dict[str, Any]], color_scheme: Optional[str]
    ) -> Dict[str, Any]:
        """Emulate a screen; ``applied`` is False when the user picked one in the pane."""
        return self._call(
            "viewport", {"tabId": None, "size": size, "colorScheme": color_scheme}
        )

    def tabs(self) -> Dict[str, Any]:
        """This chat's tabs in the pane, without the ones the user marked private."""
        return self._call("tabs", {"op": "list"})

    def select_tab(self, tab_id: str) -> Dict[str, Any]:
        return self._call("tabs", {"op": "select", "tabId": tab_id})

    # ------------------------------------------------------------- page boxes

    def dialog(self) -> Optional[Dict[str, Any]]:
        """The box the page waits on, or None."""
        self._require_op("dialog")
        return self._call("dialog", {"op": "get", "tabId": None})["dialog"]

    def answer_dialog(self, accept: bool, text: Optional[str]) -> Dict[str, Any]:
        """OK or Cancel on the page's box; ``text`` answers a prompt. The reply names the next box, if any."""
        self._require_op("dialog")
        return self._call(
            "dialog",
            {
                "op": "answer",
                "tabId": None,
                "accept": accept,
                "text": text,
                **self._scope(),
            },
        )

    def release_dialogs(self) -> int:
        """Dismiss the boxes this chat's own steps opened and left. Called when a turn ends."""
        if not self._touched:
            return 0
        self._touched = False
        if "dialog" not in self._bridge.ops:
            return 0
        return int(self._call("dialog", {"op": "release"})["dismissed"])

    def run_script(self, expression: str) -> ScriptResult:
        """browser_eval: the expression in the page's main world. Main refuses any page off loopback."""
        self._require_op("script")
        res = self._call("script", {"tabId": None, "expression": expression})
        if res.get("dialog"):
            raise PageDialogOpen(res["dialog"], done=True)
        if res["ok"]:
            return ScriptResult(True, res["json"], bool(res["truncated"]), res["url"])
        return ScriptResult(False, res["error"], False, res["url"])

    # ------------------------------------------------------------ dev servers

    def dev_servers(self) -> List[Dict[str, Any]]:
        return self._dev("list")["servers"]

    def dev_server_ensure(self, key: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Start ``spec``, or return it running; waits until its port answers."""
        # Main bounds the wait by the spec's own timeout; this only must outlast it.
        timeout = spec["readyTimeoutMs"] / 1000 + 30
        # The backend's PATH is the login shell's (apply_login_path); main's may be launchd's.
        body = {"key": key, "spec": spec, "searchPath": os.environ.get("PATH")}
        return self._dev("ensure", body, timeout_s=timeout)

    def dev_server_stop(self, key: str) -> Dict[str, Any]:
        return self._dev("stop", {"key": key})["server"]

    def dev_server_logs(
        self, key: str, *, errors_only: bool, pattern: Optional[str], limit: int
    ) -> Dict[str, Any]:
        return self._dev(
            "logs",
            {
                "key": key,
                "errorsOnly": errors_only,
                "pattern": pattern or None,
                "limit": limit,
            },
        )

    def _dev(
        self, op: str, body: Optional[Dict[str, Any]] = None, *, timeout_s=None
    ) -> Any:
        self._require_op("devServers")
        return self._call("devServers", {"op": op, **(body or {})}, timeout_s=timeout_s)

    def storage_state(self) -> Dict[str, Any]:
        raise PaneSignInByHand()

    def wait_for_login(self, url: str, *, timeout_s: float = 300.0) -> Dict[str, Any]:
        raise PaneSignInByHand()

    # --------------------------------------------------------------- internals

    def _target(self, ref: str) -> Dict[str, Any]:
        """Where ``ref`` lives: its frame and the selector the snapshot stamped on it."""
        ref_selector(ref)
        # The picker runs in the main frame only.
        if is_pick_ref(ref):
            return {"frameId": None, "selector": ref_selector(ref)}
        if ref not in self._frames_by_ref and not ref.startswith(
            f"g{self._generation}e"
        ):
            raise ElementNotFound(ref)
        return {
            "frameId": self._frames_by_ref.get(ref),
            "selector": ref_selector(in_frame_ref(ref)),
        }

    def _act(self, action: Dict[str, Any], target: Optional[Dict[str, Any]]) -> Any:
        res = self._call(
            "act", {"tabId": None, "action": action, "target": target, **self._scope()}
        )
        # The step ran, and the page now waits on a box: nothing can be read until it is answered.
        if res.get("dialog"):
            raise PageDialogOpen(res["dialog"], done=True)
        return res

    def _require_op(self, op: str) -> None:
        if self._bridge.ops and op not in self._bridge.ops:
            raise BrowserError(
                "This GAIA desktop app is older than the agent and can't do this "
                "in the browser pane. Ask the user to update GAIA desktop."
            )

    def _scope(self) -> Dict[str, List[str]]:
        return {"allowedOrigins": list(self._allowed)}

    def _check_scope(self, url: str) -> None:
        """Main checks too; this covers a page that moved while it was read."""
        origin = origin_of(url)
        if origin and not is_loopback_origin(origin) and origin not in self._allowed:
            raise PageOffScope(origin)

    def _call(
        self, op: str, body: Dict[str, Any], *, timeout_s: Optional[float] = None
    ) -> Any:
        self._touched = self._touched or body.get("op") != "release"
        # Main checks it on every op: the user may have switched chats since attach.
        return self._bridge.call(
            op, {"sessionId": self._session, **body}, timeout_s=timeout_s
        )


__all__ = [
    "LogKind",
    "MAX_LOG_LINES",
    "PANE_KEYS",
    "VIEWPORT_PRESETS",
    "PaneDriver",
    "ScriptResult",
    "Shot",
]
