# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""BrowserUseToolsMixin — a real browser the agent can act in.

Complements :class:`~gaia.agents.tools.browser_tools.BrowserToolsMixin` rather
than replacing it. ``fetch_page`` is one HTTP GET and costs ~200 ms; this
carries a live Chromium. Most reading should still go through ``fetch_page``.
This is for what that cannot do: pages behind a login, and pages that only
exist after JavaScript runs.

Six tools, deliberately. Every tool schema is ~155 prompt tokens and the model
prefills at ~387 tok/s, so each one costs ~0.4 s on every LLM call that sees
it. They live in a lazy bundle for that reason.

Talks to Playwright's Python API directly — no MCP hop, no subprocess, no JSON
round trip. The browser is launched once and reused for the session.
"""

from __future__ import annotations

import os
from typing import Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

#: Origins the agent has an authenticated session for this run. Interacting
#: with one of these is what flips a click from ungated to confirmed.
_AUTHENTICATED_MARKER = "_browser_authenticated_origins"

#: Calls that act on the page rather than only describing it. ``browser_open``
#: is here because a GET can act: an unsubscribe, logout or "delete" link is a
#: plain navigation, and inside a signed-in session that is a real change.
_ACTING = frozenset({"browser_click", "browser_type", "browser_open"})


class BrowserUseToolsMixin:
    """Live-browser tools: open, observe, act, and sign in.

    Declares its own confirmation hook, so ANY agent composing this mixin —
    including one scaffolded by ``gaia agent init --tools browser_use`` — is
    gated. It used to rely on ``ChatAgent`` overriding the decision, which left
    every other composer with an ungated browser and a saved sign-in.

    The agent owns one :class:`~gaia.browser.driver.PlaywrightDriver` for its
    lifetime, created on the first call that needs it. Call
    :meth:`cleanup_browser_use` on shutdown to close the browser.
    """

    #: Consulted by ``Agent._tool_requires_confirmation``.
    CONFIRMATION_HOOKS = ("browser_call_needs_confirmation",)

    _browser_driver = None  # PlaywrightDriver, lazily created
    _browser_headless: Optional[bool] = None
    #: Origin of the page currently open. Tracked on navigation so the
    #: confirmation gate never costs a round trip to the browser.
    _browser_current_origin: Optional[str] = None

    # ------------------------------------------------------------------ internals

    def _authenticated_origins(self) -> set:
        origins = getattr(self, _AUTHENTICATED_MARKER, None)
        if origins is None:
            origins = set()
            setattr(self, _AUTHENTICATED_MARKER, origins)
        return origins

    def _ensure_driver(self, *, headless: Optional[bool] = None):
        """Return a started driver, launching the browser on first use."""
        from gaia.browser.driver import PlaywrightDriver

        driver = self._browser_driver
        if driver is not None and driver.started:
            return driver

        # A driver that died (browser crash, user closed the window) is
        # replaced rather than reused — reusing it would raise on every call.
        if driver is not None:
            try:
                driver.close()
            except Exception as e:  # noqa: BLE001 — already dead; log and move on
                logger.debug("Discarding dead browser driver: %s", e)

        self._browser_driver = PlaywrightDriver(
            headless=self._browser_headless if headless is None else headless,
            allow_navigation=self._navigation_allowed,
        )
        self._browser_driver.start()
        return self._browser_driver

    def _restore_session_for(self, url: str) -> bool:
        """Reopen the browser with a saved session for ``url``, if one exists.

        Raises ``SessionStoreError`` when a session exists but cannot be read —
        a corrupt blob or an unreachable keyring used to be logged and swallowed,
        leaving the agent quietly signed out with the actionable message going
        nowhere. "No session stored" is the only quiet outcome.
        """
        from gaia.browser import session as session_store

        state = session_store.load(url)
        if not state:
            return False

        from gaia.browser.driver import PlaywrightDriver

        if self._browser_driver is not None:
            try:
                self._browser_driver.close()
            except Exception as e:  # noqa: BLE001 — best-effort
                logger.debug("Closing browser before session restore: %s", e)

        self._browser_driver = PlaywrightDriver(
            headless=(
                True if self._browser_headless is None else self._browser_headless
            ),
            storage_state=state,
            allow_navigation=self._navigation_allowed,
        )
        self._browser_driver.start()
        self._authenticated_origins().add(session_store.origin_of(url))
        self._browser_current_origin = session_store.origin_of(url)
        logger.info("Restored saved browser session for %s", url)
        return True

    @classmethod
    def _navigation_allowed(cls, url: str) -> bool:
        """Predicate form of :meth:`_check_navigable`, for the driver's guard."""
        return cls._check_navigable(url) is None

    @staticmethod
    def _check_navigable(url: str) -> Optional[str]:
        """Reject a URL the browser must not visit. Returns an error, or None.

        Routes through ``WebClient.validate_url`` — the single authority for
        "is this address safe to reach" — so the live browser gets the same
        SSRF screening ``fetch_page`` already has: http/https only, no blocked
        ports, and no private, loopback, link-local or reserved address. That
        last one is what keeps a page from steering the agent at a cloud
        metadata endpoint.

        Set ``GAIA_BROWSER_ALLOW_PRIVATE=1`` to permit private addresses when
        the point is to drive a local dev server. Deliberately explicit: the
        default denies, and the opt-in is visible in the environment.

        This screens the URL the agent asks for; the driver additionally
        routes every top-level navigation through :meth:`_navigation_allowed`,
        so a redirect or a clicked link is screened too.
        """
        if not url.startswith(("http://", "https://")):
            return (
                f"Error: invalid URL {url!r}. It must start with http:// "
                "or https://."
            )
        if os.getenv("GAIA_BROWSER_ALLOW_PRIVATE", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return None
        from gaia.web.client import WebClient

        try:
            WebClient().validate_url(url)
        except ValueError as e:
            return (
                f"Error: refusing to open {url} — {e}\n"
                "  This address is not on the public internet. Set "
                "GAIA_BROWSER_ALLOW_PRIVATE=1 to allow local addresses."
            )
        return None

    def browser_origin_is_authenticated(self, url: str) -> bool:
        """True when this run holds a signed-in session for ``url``'s origin.

        Read by the agent's confirmation gate: acting inside someone's logged-in
        account is the case that needs a human, not browsing the open web.
        """
        try:
            from gaia.browser import session as session_store

            return session_store.origin_of(url) in self._authenticated_origins()
        except Exception:  # noqa: BLE001 — a bad URL is not authenticated
            return False

    def _note_location(self, snap: dict) -> None:
        """Record the origin the browser actually ended up on.

        Called after every navigating action, not just ``browser_open``: a
        click can leave the site entirely, and a gate reading a stale origin
        would wave through actions on a signed-in site the agent arrived at by
        following a link.

        A page can also land somewhere that has no origin worth tracking
        (``about:blank``, a data URL). That is not a failure of the
        navigation, so it clears the origin rather than raising — which is
        also the safe direction, since an unknown origin is never treated as
        authenticated.
        """
        from gaia.browser import session as session_store
        from gaia.browser.errors import SessionStoreError

        url = (snap or {}).get("url") or ""
        try:
            self._browser_current_origin = session_store.origin_of(url)
        except SessionStoreError:
            self._browser_current_origin = None

    def browser_call_needs_confirmation(self, tool_name: str) -> bool:
        """Whether this browser call must be confirmed by the user.

        Reading the open web is ungated — it is what ``fetch_page`` already
        does. What earns a prompt is acting *inside someone's signed-in
        session*, where a page carrying a prompt injection could otherwise talk
        the model into something consequential. Signing in is always confirmed:
        it opens a window and persists a session.

        "Signed in" is answered by the **browser context**, not by the URL the
        user signed in at. Cookies are context-wide, so a login at
        ``accounts.google.com`` authenticates ``mail.google.com`` too; matching
        against the login origin missed every identity-provider split, which is
        most real sign-ins.

        Fails **closed**: if the browser cannot be asked, the call is treated as
        authenticated and prompts. A gate that opens when it is confused is not
        a gate.

        Named as a hook rather than an override because ``Agent`` precedes the
        tool mixins in the MRO — see ``Agent.CONFIRMATION_HOOKS``.
        """
        if tool_name == "browser_login":
            return True
        if tool_name not in _ACTING:
            return False

        origin = getattr(self, "_browser_current_origin", None)
        if not origin:
            return False
        if origin in self._authenticated_origins():
            return True

        # Cookie presence alone does NOT mean signed in — almost every site
        # sets one, and treating that as a session gated ordinary browsing: a
        # live run was stopped for confirmation on a public weather page.
        # So the context is only consulted once a sign-in has actually
        # happened this run, which is what makes sibling origins of that login
        # (mail.google.com after accounts.google.com) gate without dragging in
        # every unrelated site.
        if not self._authenticated_origins():
            return False

        driver = self._browser_driver
        if driver is None or not driver.started:
            return False
        try:
            return driver.origin_has_cookies(origin)
        except Exception as e:  # noqa: BLE001 — see "fails closed" above
            logger.warning(
                "Could not check sign-in state for %s (%s); requiring confirmation.",
                origin,
                e,
            )
            return True

    def cleanup_browser_use(self) -> None:
        """Close the browser. Safe to call when none was ever opened."""
        driver = self._browser_driver
        self._browser_driver = None
        if driver is None:
            return
        try:
            driver.close()
        except Exception as e:  # noqa: BLE001 — shutdown is best-effort
            logger.debug("Browser cleanup: %s", e)

    # ------------------------------------------------------------- registration

    def register_browser_use_tools(self) -> None:
        """Register the live-browser tools."""
        from gaia.agents.base.tools import tool
        from gaia.browser import driver as browser_driver

        mixin = self

        if not browser_driver.installed():
            # Registering tools whose backend can never work would spend prompt
            # tokens on an unusable capability and give the model something to
            # fail with. Say why, once, and register nothing.
            logger.info(
                "Browser-use tools not registered: Playwright is not installed. "
                "Install with: pip install 'amd-gaia[browser]' && "
                "python -m playwright install chromium"
            )
            return

        from gaia.browser import session as session_store
        from gaia.browser.driver import DEFAULT_LOGIN_TIMEOUT_S
        from gaia.browser.errors import BrowserError
        from gaia.browser.snapshot import render

        def _fail(e: Exception) -> str:
            # BrowserError messages are already written for the model: what
            # failed, what to do, where to look. Anything else gets a prefix so
            # the model can tell a browser failure from a tool-arg mistake.
            return str(e) if isinstance(e, BrowserError) else f"Browser error: {e}"

        @tool(atomic=True)
        def browser_open(url: str) -> str:
            """Open a URL in a real browser and return the page's interactive elements.

            Use this when a page needs JavaScript to render, or is behind a
            login. For plain articles and documentation prefer fetch_page —
            it is much faster and needs no browser.

            Reuses a saved sign-in for the site automatically when one exists.
            Returns the page title, URL, a numbered list of interactive
            elements (refs like e1, e2 — pass these to browser_click and
            browser_type), and the readable page text.

            Args:
                url: Full URL to open (must start with http:// or https://)
            """
            bad = mixin._check_navigable(url)
            if bad:
                return bad
            try:
                # Restore whenever this origin has a stored session and this run
                # has not already loaded it — NOT only on the first browser call.
                # The common trajectory is "read something, then go to the site
                # that needs the account"; gating on a cold driver meant the
                # saved sign-in was skipped exactly then, and the agent landed
                # on a login screen. Rebuilding the context costs one browser
                # launch on a path that otherwise costs a human sign-in.
                if not mixin.browser_origin_is_authenticated(url):
                    mixin._restore_session_for(url)
                driver = mixin._ensure_driver()
                snap = driver.goto(url)
                mixin._note_location(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_open(%s) failed: %s", url, e)
                return _fail(e)
            return render(snap)

        @tool(atomic=True)
        def browser_snapshot() -> str:
            """Re-read the current page and return its interactive elements.

            Call this after anything that changes the page, or when a ref from
            an earlier snapshot no longer works. Refs are only valid until the
            next snapshot.
            """
            try:
                driver = mixin._ensure_driver()
                snap = driver.snapshot()
                mixin._note_location(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_snapshot failed: %s", e)
                return _fail(e)
            return render(snap)

        @tool(atomic=True)
        def browser_click(ref: str) -> str:
            """Click an element on the current page.

            Args:
                ref: Element ref from browser_snapshot (e.g. 'e7'), not a CSS
                     selector and not screen coordinates
            """
            try:
                driver = mixin._ensure_driver()
                snap = driver.click(ref)
                mixin._note_location(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_click(%s) failed: %s", ref, e)
                return _fail(e)
            return f"Clicked {ref}.\n\n" + render(snap)

        @tool(atomic=True)
        def browser_type(ref: str, text: str, press_enter: bool = False) -> str:
            """Type text into a field, or choose an option in a dropdown.

            Never type a password with this tool — use browser_login, which
            hands the keyboard to the user so the password is never seen.

            Args:
                ref: Element ref from browser_snapshot (e.g. 'e3')
                text: Text to enter, or the option label to select
                press_enter: Press Enter afterwards — use to submit a search box
            """
            try:
                driver = mixin._ensure_driver()
                snap = driver.type_text(ref, text, press_enter=press_enter)
                mixin._note_location(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_type(%s) failed: %s", ref, e)
                return _fail(e)
            suffix = " and pressed Enter" if press_enter else ""
            return f"Typed into {ref}{suffix}.\n\n" + render(snap)

        @tool(atomic=True, timeout=DEFAULT_LOGIN_TIMEOUT_S + 60)
        def browser_login(url: str) -> str:
            """Ask the user to sign in to a site, then save the session.

            Opens a visible browser window at the sign-in page and waits for
            the user to authenticate themselves. GAIA never sees or types the
            password — the user does, along with any MFA step. Once signed in,
            the session is saved encrypted so later runs skip this.

            Use when a page says the user is signed out, or when browser_open
            returns a login screen instead of the content asked for.

            Args:
                url: The site's sign-in URL
            """
            bad = mixin._check_navigable(url)
            if bad:
                return bad
            try:
                # A headed window is the whole mechanism — the user cannot sign
                # in to something they cannot see.
                if mixin._browser_driver is not None:
                    mixin.cleanup_browser_use()
                driver = mixin._ensure_driver(headless=False)
                snap = driver.wait_for_login(url)
                state = driver.storage_state()
                origin = session_store.save(url, state)
                mixin._authenticated_origins().add(origin)
                mixin._browser_current_origin = origin
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_login(%s) failed: %s", url, e)
                return _fail(e)
            return (
                f"Signed in to {origin} and saved the session. "
                "Later runs will reuse it without asking.\n\n" + render(snap)
            )

        @tool(atomic=True)
        def browser_sessions() -> str:
            """List the sites GAIA has a saved sign-in for.

            Shows only which sites and when they were saved — never cookies or
            any credential.
            """
            try:
                rows = session_store.listing()
            except Exception as e:  # noqa: BLE001 — returned to the model
                return _fail(e)
            if not rows:
                return (
                    "No saved browser sign-ins. Use browser_login(url) to sign "
                    "in to a site."
                )
            lines = [f"Saved browser sign-ins ({len(rows)}):"]
            for r in rows:
                lines.append(
                    f"  {r.get('origin', '?')} — saved {r.get('saved_at', '?')}, "
                    f"{r.get('cookies', 0)} cookies"
                )
            return "\n".join(lines)
