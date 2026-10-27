# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Errors for the browser-use driver.

Every message names what failed, what the caller should do, and where to look
next — the browser is the most failure-prone thing GAIA drives, and a bare
``TimeoutError`` from deep inside Playwright tells a user nothing.
"""

from __future__ import annotations

from gaia.browser.snapshot import is_pick_ref


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
        if is_pick_ref(ref):
            super().__init__(
                f"Element '{ref}' the user picked is not on this page any more: "
                "the page reloaded or changed, or it was picked in another tab. "
                "Ask the user to pick it again, or call browser_snapshot() and "
                "use a ref from that."
            )
            return
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


class BrowserUnavailable(BrowserError):
    """No browser can serve this chat: no desktop pane for it, no Playwright."""

    def __init__(self) -> None:
        super().__init__(
            "No browser is available to this chat.\n"
            "  Open this chat in GAIA desktop to use its browser pane, or install "
            "a headless browser with:  pip install 'amd-gaia[browser]' "
            "&& python -m playwright install chromium\n"
            "  Docs: https://amd-gaia.ai/docs/guides/browser-use"
        )


class PaneUnreachable(BrowserError):
    """GAIA desktop advertises a browser pane, but its bridge did not answer.

    Never a reason to switch to the headless browser: the user is looking at
    the pane, and a hidden browser acting instead would be invisible to them.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"The browser pane in GAIA desktop is not responding: {detail}\n"
            "  Restart GAIA desktop, then try again. Its log is "
            "~/.gaia/electron-main.log."
        )


class PaneProtocolMismatch(BrowserError):
    """The desktop app and this Python package speak different bridge versions."""

    def __init__(self, app_protocol: int, ours: int) -> None:
        side = "GAIA desktop" if app_protocol < ours else "the GAIA Python package"
        super().__init__(
            f"GAIA desktop's browser pane speaks bridge version {app_protocol}, "
            f"and this GAIA backend speaks version {ours}.\n"
            f"  Update {side} so both match (`gaia --version` shows the backend's "
            "version)."
        )


class PageOffScope(BrowserError):
    """The pane's page is on an origin this call was not approved to read."""

    def __init__(self, origin: str) -> None:
        super().__init__(
            f"The page in the browser pane is now on {origin}, which this step was "
            "not allowed to read, so its content was withheld.\n"
            "  Call browser_snapshot to read it; GAIA will ask the user first."
        )


class PaneNavigationBlocked(BrowserError):
    """The page tried to leave the approved sites during an agent step."""

    def __init__(self, url: str, origin: str) -> None:
        super().__init__(
            f"Stopped the browser pane from going to {url}: {origin} was not "
            "approved for this step.\n"
            f"  To open it, call browser_open('{url}'); GAIA will ask the user first."
        )


class PaneSignInByHand(BrowserError):
    """The browser pane never signs in for the user; they sign in there themselves."""

    def __init__(self) -> None:
        super().__init__(
            "Signing in is not done for you in the browser pane. Ask the user to "
            "sign in to the site themselves in the pane, then tell you when they "
            "are done. The pane keeps that sign-in."
        )


def describe_dialog(dialog: dict) -> str:
    """One sentence naming a page box and how browser_dialog answers it, for the model."""
    kind = dialog.get("type")
    message = str(dialog.get("message") or "").strip()
    quoted = f' "{message}"' if message else ""
    if kind == "alert":
        return f"The page shows a notice{quoted}. Close it with browser_dialog(accept=True)."
    if kind == "prompt":
        return (
            f"The page asks for text{quoted}. Answer with "
            "browser_dialog(accept=True, text='...'), or cancel with "
            "browser_dialog(accept=False)."
        )
    if kind == "beforeunload":
        return (
            "The page asks before leaving it: changes there may not be saved. "
            "Leave with browser_dialog(accept=True), or stay with "
            "browser_dialog(accept=False)."
        )
    return (
        f"The page asks{quoted}. Press OK with browser_dialog(accept=True), or "
        "Cancel with browser_dialog(accept=False)."
    )


class PageDialogOpen(BrowserError):
    """The pane's page is waiting on a box (alert, confirm, prompt, leave this page?).

    ``done`` is True when the step itself ran and opened the box, False when
    the box was already open and nothing was done.
    """

    def __init__(self, dialog: dict, *, done: bool) -> None:
        self.dialog = dialog
        self.done = done
        lead = "" if done else "Nothing was done: a box is open on the page. "
        super().__init__(lead + describe_dialog(dialog))
