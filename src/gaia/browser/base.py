# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The browser contract the browser-use tools speak.

Two implementations: :class:`~gaia.browser.driver.PlaywrightDriver`, a
headless Chromium for the terminal UI, the CLI and the web build, and
:class:`~gaia.browser.pane_driver.PaneDriver`, the desktop app's browser pane.
The tools never branch on which one they hold; ``where`` is the only thing
that differs in what they show the model.
"""

from __future__ import annotations

from typing import Any, Dict, Protocol

#: The shape ``_SNAPSHOT_JS`` returns after ``snapshot.merge_frames``.
Snapshot = Dict[str, Any]


class BrowserDriver(Protocol):
    """What ``BrowserUseToolsMixin`` calls. Every acting method returns a fresh snapshot."""

    @property
    def where(self) -> str:
        """First line of every tool result: "(in the browser pane)" or "(headless browser)"."""

    @property
    def started(self) -> bool: ...

    def close(self) -> None: ...

    def goto(self, url: str) -> Snapshot: ...

    def snapshot(self) -> Snapshot: ...

    def click(self, ref: str) -> Snapshot: ...

    def type_text(
        self, ref: str, text: str, *, press_enter: bool = False
    ) -> Snapshot: ...

    def back(self) -> Snapshot: ...

    def find(self, query: str, context: int = 160) -> Dict[str, Any]: ...

    def current_url(self) -> str: ...

    def origin_has_cookies(self, origin: str) -> bool: ...

    def storage_state(self) -> Dict[str, Any]: ...

    def wait_for_login(self, url: str, *, timeout_s: float = ...) -> Snapshot: ...


__all__ = ["BrowserDriver", "Snapshot"]
