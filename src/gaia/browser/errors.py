# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Errors for the browser-use driver.

Every message names what failed, what the caller should do, and where to look
next — the browser is the most failure-prone thing GAIA drives, and a bare
``TimeoutError`` from deep inside Playwright tells a user nothing.
"""

from __future__ import annotations


class BrowserError(Exception):
    """Base for every browser-use failure."""


class BrowserNotInstalled(BrowserError):
    """Playwright, or its browser binary, is missing."""

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            "Browser automation is not installed.\n"
            "  Install it with:  pip install 'amd-gaia[browser]' "
            "&& python -m playwright install chromium\n"
            "  Docs: https://amd-gaia.ai/docs/guides/browser-use"
            + (f"\n  Detail: {detail}" if detail else "")
        )


class BrowserLaunchFailed(BrowserError):
    """The browser process would not start."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Could not start the browser: {detail}\n"
            "  Run `python -m playwright install chromium` to repair the "
            "browser install, or set GAIA_BROWSER_HEADLESS=1 if no display "
            "is available."
        )


class BrowserNotStarted(BrowserError):
    """A page operation was attempted before the browser was started."""

    def __init__(self) -> None:
        super().__init__(
            "No browser session is open. Call browser_open(url) first — it "
            "starts the browser and loads the page."
        )


class NavigationFailed(BrowserError):
    """A navigation did not complete."""

    def __init__(self, url: str, detail: str) -> None:
        super().__init__(
            f"Could not load {url}: {detail}\n"
            "  Check the URL is reachable, or try fetch_page(url) for a "
            "static read that needs no browser."
        )


class NavigationBlocked(BrowserError):
    """A navigation was refused because it left the public internet."""

    def __init__(self, requested: str, blocked: str) -> None:
        super().__init__(
            f"Refused to load {requested}: it tried to reach {blocked}, which "
            "is not on the public internet.\n"
            "  A page cannot send the browser to a private or link-local "
            "address. Set GAIA_BROWSER_ALLOW_PRIVATE=1 to allow local "
            "addresses deliberately."
        )


class ElementNotFound(BrowserError):
    """A ref no longer resolves to an element on the page."""

    def __init__(self, ref: str) -> None:
        super().__init__(
            f"Element '{ref}' is not on the page any more. The page changed "
            "since the last snapshot — call browser_snapshot() to get current "
            "refs, then retry."
        )


class InteractionFailed(BrowserError):
    """An element was found but would not accept the interaction."""

    def __init__(self, ref: str, action: str, detail: str) -> None:
        super().__init__(
            f"Could not {action} element '{ref}': {detail}\n"
            "  The element may be disabled, covered by an overlay, or still "
            "loading. Call browser_snapshot() to re-read the page."
        )


class LoginTimedOut(BrowserError):
    """The user did not finish signing in inside the window allowed."""

    def __init__(self, url: str, timeout_s: float) -> None:
        super().__init__(
            f"Sign-in at {url} was not completed within {timeout_s:g}s.\n"
            "  Run browser_login(url) again when ready — nothing was saved."
        )


class SessionStoreError(BrowserError):
    """Saved session state could not be read or written."""
