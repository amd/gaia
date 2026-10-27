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

Two browsers, one rule (:meth:`BrowserUseToolsMixin._pane`). A chat open in
GAIA desktop's window uses the desktop app's browser pane, which the user can
see; every other chat uses a headless Chromium through Playwright's Python API.
Every tool result's first line names which one answered.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, List, Optional, Tuple

from gaia.agents.base.tool_grants import SITE_ARG
from gaia.browser.errors import BrowserError
from gaia.logger import get_logger

if TYPE_CHECKING:
    from gaia.browser.base import BrowserDriver

logger = get_logger(__name__)

#: Origins the agent has an authenticated session for this run. Interacting
#: with one of these is what flips a click from ungated to confirmed.
_AUTHENTICATED_MARKER = "_browser_authenticated_origins"

#: Calls that act on the page rather than only describing it. ``browser_open``
#: is here because a GET can act: an unsubscribe, logout or "delete" link is a
#: plain navigation, and inside a signed-in session that is a real change.
_ACTING = frozenset({"browser_click", "browser_type", "browser_open"})

#: Phrases in a control's own label that mark an action you cannot take back —
#: money moved, an account destroyed, something published.
#:
#: Deliberately phrases, not verbs. Bare "submit", "confirm", "save", "send",
#: "apply", "continue" and "order" are the most common words on the web —
#: gating them would put a prompt in front of every search box and sort
#: control, and a gate that fires constantly is one people learn to click
#: through. "Order by price" must not prompt; "Place order" must.
#:
#: Matched as whole words against the element's accessible name, so "reorder"
#: and "in order to" do not trip "order".
_IRREVERSIBLE_PHRASES = (
    # money
    "pay now",
    "pay and",
    "confirm payment",
    "complete purchase",
    "purchase",
    "buy now",
    "place order",
    "order now",
    "confirm order",
    "checkout",
    "check out",
    "transfer",
    "withdraw",
    "send money",
    "donate",
    # destruction
    "delete",
    "permanently",
    "deactivate",
    "close account",
    "terminate",
    "erase",
    "wipe",
    "cancel subscription",
    "unsubscribe",
    # publishing / outbound
    "send email",
    "send message",
    "publish",
    "send invite",
)


#: Pane tools that read a page. Off loopback, each asks before it reads a site.
_PANE_SITE_TOOLS = frozenset(
    {
        "browser_open",
        "browser_snapshot",
        "browser_find",
        "browser_back",
        "browser_screenshot",
        "browser_logs",
    }
)


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
    #: ref -> accessible name, from the last snapshot.
    _browser_last_elements: Optional[dict] = None
    #: The pane serving this chat, or None for headless; read once (see _pane).
    _browser_route = None
    _browser_routed: bool = False
    #: (tool, origin) the site gate just asked about; the tool body takes it.
    _site_pending: Optional[Tuple[str, Optional[str]]] = None
    #: (tool, id(args), origin) worked out for the prompt, reused by the gate.
    _site_memo: Optional[Tuple[str, int, Optional[str]]] = None

    # ------------------------------------------------------------------ internals

    def _authenticated_origins(self) -> set:
        origins = getattr(self, _AUTHENTICATED_MARKER, None)
        if origins is None:
            origins = set()
            setattr(self, _AUTHENTICATED_MARKER, origins)
        return origins

    def _pane(self):
        """The browser pane serving this chat, or None to use the headless browser.

        The one routing rule, decided once per agent. A chat with no Agent UI
        session, no running desktop app (no discovery file, or its process is
        gone), or an Agent UI session that is not the chat open in the desktop
        window goes headless. A desktop app that is running but does not answer
        raises ``PaneUnreachable`` and is asked again next call: the user is
        looking at the pane, so a hidden headless browser must never act instead.
        """
        if self._browser_routed:
            return self._browser_route
        route = None
        session_id = getattr(getattr(self, "config", None), "ui_session_id", None)
        if session_id:
            from gaia.browser._pane_bridge import PaneBridge, discover
            from gaia.browser.pane_driver import PaneDriver

            disc = discover()
            if disc is not None:
                bridge = PaneBridge(disc)
                bridge.hello()
                if bridge.attach(session_id)["status"] == "attached":
                    route = PaneDriver(bridge, session_id)
        self._browser_route = route
        self._browser_routed = True
        if route is not None:
            logger.info("Browser tools use GAIA desktop's browser pane for this chat")
        return route

    def _ensure_driver(self, *, headless: Optional[bool] = None):
        """Return a started driver: the pane by :meth:`_pane`, else a headless browser."""
        from gaia.browser import driver as browser_driver
        from gaia.browser.errors import BrowserUnavailable

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

        # A headed window is only ever asked for by browser_login, which the pane refuses.
        if headless is None:
            pane = self._pane()
            if pane is not None:
                self._browser_driver = pane
                return pane

        if not browser_driver.installed():
            raise BrowserUnavailable()
        self._browser_driver = browser_driver.PlaywrightDriver(
            headless=self._browser_headless if headless is None else headless,
            allow_navigation=self._navigation_allowed,
        )
        self._browser_driver.start()
        return self._browser_driver

    @contextmanager
    def _reading(self, tool_name: str) -> Iterator["BrowserDriver"]:
        """The driver for one tool call, scoped to the site its gate approved.

        A pane op may read loopback plus the one origin the user approved for
        this call; main refuses any other page, so content the user was never
        asked about cannot reach the model even if the gate was skipped.
        """
        from gaia.browser.pane_driver import PaneDriver

        driver = self._ensure_driver()
        approved = self._take_site_approval(tool_name)
        if isinstance(driver, PaneDriver):
            with driver.scoped(approved):
                yield driver
        else:
            yield driver

    def _take_site_approval(self, tool_name: str) -> List[str]:
        """The origin this call's gate asked about, once. Only a running tool body takes it."""
        pending, self._site_pending = self._site_pending, None
        if pending and pending[0] == tool_name and pending[1]:
            return [pending[1]]
        return []

    def _pane_site(
        self, pane, tool_name: str, tool_args: Optional[dict]
    ) -> Optional[str]:
        """The non-loopback origin this pane call would read, or None.

        ``browser_open`` reads the site it is asked to open; every other tool
        reads whatever page the pane shows now, which the user may have
        changed. A lookup failure returns None: no approval is recorded, so
        main then refuses any page off loopback for this call.
        """
        from gaia.browser._pane_bridge import is_loopback_origin, origin_of

        args = tool_args if isinstance(tool_args, dict) else {}
        memo = self._site_memo
        self._site_memo = None
        if memo is not None and memo[:2] == (tool_name, id(tool_args)):
            return memo[2]
        if tool_name == "browser_open":
            origin = origin_of(str(args.get("url") or ""))
        else:
            try:
                origin = pane.current_origin()
            except BrowserError as e:
                logger.warning("Could not read the pane's page origin: %s", e)
                return None
        if not origin or is_loopback_origin(origin):
            return None
        return origin

    def confirmation_args_for_call(self, tool_name: str, tool_args):
        """The prompt's arguments, plus the site a pane call would read (``SITE_ARG``).

        Display and grant scope only; the tool runs with its own arguments.
        Cooperative: other mixins add their own fields through ``super()``.
        """
        parent = getattr(super(), "confirmation_args_for_call", None)
        shown = parent(tool_name, tool_args) if callable(parent) else tool_args
        if tool_name not in _PANE_SITE_TOOLS or not isinstance(tool_args, dict):
            return shown
        try:
            pane = self._pane()
        except BrowserError:
            return shown  # the tool body raises this before reading anything
        if pane is None:
            return shown
        origin = self._pane_site(pane, tool_name, tool_args)
        self._site_memo = (tool_name, id(tool_args), origin)
        return {**shown, SITE_ARG: origin} if origin else shown

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

    def _note_elements(self, snap: dict) -> None:
        """Remember ref -> label so the gate can see what it is about to click.

        The confirmation decision happens before the tool body runs and only
        has the ref, so the label has to be carried over from the snapshot
        that produced it.
        """
        self._browser_last_elements = {
            e.get("ref"): e.get("name") or ""
            for e in (snap or {}).get("elements") or []
            if e.get("ref")
        }

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

    def _element_label(self, ref: str) -> str:
        """Accessible name of ``ref`` from the last snapshot, or ""."""
        return (getattr(self, "_browser_last_elements", None) or {}).get(ref, "")

    @staticmethod
    def _looks_irreversible(label: str) -> bool:
        """Whether a control's own label names an action that cannot be undone."""
        if not label:
            return False
        text = " " + re.sub(r"[^a-z0-9]+", " ", label.lower()).strip() + " "
        return any(f" {p} " in text for p in _IRREVERSIBLE_PHRASES)

    def browser_call_needs_confirmation(
        self, tool_name: str, tool_args: Optional[dict] = None
    ) -> bool:
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
        try:
            pane = self._pane()
        except BrowserError as e:
            # Not a gate opening: the tool body raises this same error before
            # it reads or does anything, and main serves no page off loopback
            # to a call nobody approved.
            logger.warning("Browser pane unreachable while gating %s: %s", tool_name, e)
            return False
        if pane is not None:
            return self._pane_needs_confirmation(pane, tool_name, tool_args)

        if tool_name == "browser_login":
            return True
        if tool_name not in _ACTING:
            return False

        # Irreversible by its own label — gate regardless of sign-in state.
        # A transfer or a deletion is no more undoable on a site you happen not
        # to be signed into, and the live suite clicked "Send transfer now"
        # unprompted precisely because no session existed.
        ref = (tool_args or {}).get("ref")
        if ref and self._looks_irreversible(self._element_label(str(ref))):
            return True

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

    def _pane_needs_confirmation(
        self, pane, tool_name: str, tool_args: Optional[dict]
    ) -> bool:
        """The site gate: a pane call asks before it reads a page off loopback.

        "Always" is a ``_site_scope`` grant on that origin, answered by the
        console before any prompt. Tools the pane refuses (sign-in, clicking
        and typing for now) are not asked about; their bodies refuse.
        """
        if tool_name not in _PANE_SITE_TOOLS:
            return False
        origin = self._pane_site(pane, tool_name, tool_args)
        self._site_pending = (tool_name, origin)
        return origin is not None

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

    def _let_read(self, path: str) -> None:
        """Let this chat read a file the agent itself just wrote, and nothing beside it."""
        from gaia.agents.tools.search_scope import path_validator_of

        validator = path_validator_of(self)
        if validator is not None:
            validator.add_read_only_path(path)

    def _pane_at_startup(self) -> Tuple[bool, bool]:
        """``(advertised, attached)`` for this chat when the agent is built.

        Advertised: GAIA desktop is running and offers a pane. Attached: this
        chat is the one open in its window, so the pane serves it.
        """
        from gaia.browser._pane_bridge import discover

        if not getattr(getattr(self, "config", None), "ui_session_id", None):
            return False, False
        try:
            if discover() is None:
                return False, False
            return True, self._pane() is not None
        except BrowserError as e:
            logger.warning(
                "GAIA desktop offers a browser pane but it did not answer (%s); the "
                "browser tools will report it when used.",
                e,
            )
            return True, False

    def register_browser_use_tools(self) -> None:
        """Register the live-browser tools.

        They register when Playwright is installed, or when this chat is open
        in GAIA desktop, whose browser pane needs no Playwright. With neither,
        nothing registers and the log names both remedies. Tools only the pane
        can serve register on this agent alone, never in the shared registry,
        so a chat outside the desktop app is never offered them.
        """
        from gaia.agents.base.tools import tool
        from gaia.browser import driver as browser_driver

        mixin = self
        installed = browser_driver.installed()
        advertised, attached = self._pane_at_startup()

        if not installed and not advertised:
            # Registering tools whose backend can never work would spend prompt
            # tokens on an unusable capability and give the model something to
            # fail with. Say why, once, and register nothing.
            logger.info(
                "Browser-use tools not registered: this chat is not open in GAIA "
                "desktop's browser pane, and Playwright is not installed. Open the "
                "chat in GAIA desktop, or install with: pip install "
                "'amd-gaia[browser]' && python -m playwright install chromium"
            )
            return

        # Without Playwright these exist only because of this chat's pane, so
        # they must not leak into the shared registry another agent snapshots.
        here = getattr(self, "_tool_overrides", None)
        shared = None if installed else here

        from gaia.browser import session as session_store
        from gaia.browser.driver import DEFAULT_LOGIN_TIMEOUT_S
        from gaia.browser.snapshot import render

        def _fail(e: Exception) -> str:
            # BrowserError messages are already written for the model: what
            # failed, what to do, where to look. Anything else gets a prefix so
            # the model can tell a browser failure from a tool-arg mistake.
            msg = str(e) if isinstance(e, BrowserError) else f"Browser error: {e}"
            where = getattr(mixin._browser_driver, "where", None)
            return f"{where}\n{msg}" if where else msg

        @tool(atomic=True, registry=shared)
        def browser_open(url: str) -> str:
            """Open a URL in a real browser and list its interactive elements.

            Use when a page needs JavaScript or is behind a login; prefer
            fetch_page for plain articles and docs — far faster, no browser.
            Reuses a saved sign-in for the site when one exists.

            Returns the title, URL, numbered element refs (e1, e2 — pass to
            browser_click and browser_type), and the readable page text.

            Args:
                url: Full URL to open (must start with http:// or https://)
            """
            try:
                pane = mixin._pane()
                if pane is None:
                    bad = mixin._check_navigable(url)
                    if bad:
                        return bad
                    # Restore whenever this origin has a stored session and this
                    # run has not already loaded it — NOT only on the first
                    # browser call. The common trajectory is "read something,
                    # then go to the site that needs the account"; gating on a
                    # cold driver meant the saved sign-in was skipped exactly
                    # then, and the agent landed on a login screen.
                    if not mixin.browser_origin_is_authenticated(url):
                        mixin._restore_session_for(url)
                elif not url.startswith(("http://", "https://")):
                    return (
                        f"Error: invalid URL {url!r}. It must start with http:// "
                        "or https://."
                    )
                with mixin._reading("browser_open") as driver:
                    snap = driver.goto(url)
                mixin._note_location(snap)
                mixin._note_elements(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_open(%s) failed: %s", url, e)
                return _fail(e)
            return f"{driver.where}\n{render(snap)}"

        @tool(atomic=True, registry=shared)
        def browser_snapshot() -> str:
            """Read the page open in the browser: its title, URL, text and interactive elements.

            In GAIA desktop this is the page in the browser pane beside the
            chat. Call it again after anything that changes the page, or when
            a ref from an earlier snapshot no longer works. Refs are only valid
            until the next snapshot.
            """
            try:
                with mixin._reading("browser_snapshot") as driver:
                    snap = driver.snapshot()
                mixin._note_location(snap)
                mixin._note_elements(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_snapshot failed: %s", e)
                return _fail(e)
            return f"{driver.where}\n{render(snap)}"

        @tool(atomic=True, registry=shared)
        def browser_click(ref: str) -> str:
            """Click an element on the current page.

            Args:
                ref: Element ref from browser_snapshot (e.g. 'e7'), not a CSS
                     selector and not screen coordinates
            """
            try:
                with mixin._reading("browser_click") as driver:
                    snap = driver.click(ref)
                mixin._note_location(snap)
                mixin._note_elements(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_click(%s) failed: %s", ref, e)
                return _fail(e)
            return f"{driver.where}\nClicked {ref}.\n\n" + render(snap)

        @tool(atomic=True, registry=shared)
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
                with mixin._reading("browser_type") as driver:
                    snap = driver.type_text(ref, text, press_enter=press_enter)
                mixin._note_location(snap)
                mixin._note_elements(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_type(%s) failed: %s", ref, e)
                return _fail(e)
            suffix = " and pressed Enter" if press_enter else ""
            return f"{driver.where}\nTyped into {ref}{suffix}.\n\n" + render(snap)

        @tool(atomic=True, timeout=DEFAULT_LOGIN_TIMEOUT_S + 60, registry=shared)
        def browser_login(url: str) -> str:
            """Ask the user to sign in to a site, then save the session.

            Opens a visible window at the sign-in page and waits for the user
            to authenticate. GAIA never sees or types the password or any MFA
            step. The session is then saved encrypted, so later runs skip it.

            Use when a page says the user is signed out, or when browser_open
            returns a login screen instead of the content asked for.

            Args:
                url: The site's sign-in URL
            """
            try:
                if mixin._pane() is not None:
                    return (
                        "(in the browser pane)\nSigning in is not done for you in "
                        "the browser pane. Ask the user to sign in to the site "
                        "themselves in the pane, then tell you when they are done. "
                        "The pane keeps that sign-in."
                    )
                bad = mixin._check_navigable(url)
                if bad:
                    return bad
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
                f"{driver.where}\nSigned in to {origin} and saved the session. "
                "Later runs will reuse it without asking.\n\n" + render(snap)
            )

        @tool(atomic=True, registry=shared)
        def browser_back() -> str:
            """Go back to the previous page, or close a tab and return to its opener.

            Use when a path turned out to be a dead end, or after reading a
            page that opened in a new tab and you need the one you came from.

            Returns the interactive elements of the page you land on.
            """
            try:
                with mixin._reading("browser_back") as driver:
                    snap = driver.back()
                mixin._note_location(snap)
                mixin._note_elements(snap)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_back failed: %s", e)
                return _fail(e)
            return f"{driver.where}\nWent back.\n\n" + render(snap)

        @tool(atomic=True, registry=shared)
        def browser_find(text: str) -> str:
            """Search the current page for text and return what surrounds it.

            A snapshot shows only the first part of a long page, so use this
            when what you need is not in it — a row far down a long list, a
            figure in a wall of text. Searches the whole page, not just the
            part the snapshot showed.

            Args:
                text: The text to look for (case-insensitive)
            """
            if not (text or "").strip():
                return "Error: give some text to search for."
            try:
                with mixin._reading("browser_find") as driver:
                    res = driver.find(text)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_find(%s) failed: %s", text, e)
                return _fail(e)
            hits = res.get("matches") or []
            if not hits:
                return (
                    f"{driver.where}\n"
                    f'"{text}" does not appear on this page '
                    f'({res.get("page_chars", 0):,} characters searched).'
                )
            lines = [driver.where, f'Found "{text}" {len(hits)} time(s):']
            for h in hits:
                lines.append(f"  … {h} …")
            return "\n".join(lines)

        @tool(atomic=True, registry=shared)
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

        if attached:
            self._register_pane_inspect_tools(tool, here, _fail)

    def _register_pane_inspect_tools(self, tool, registry, fail) -> None:
        """Tools only the browser pane can serve, on this agent alone."""
        mixin = self

        @tool(atomic=True, registry=registry)
        def browser_screenshot(ref: str = "") -> dict:
            """Take a picture of the page open in the browser pane.

            Use to check how a page looks: layout, colors, images. To read
            its text or find its buttons, use browser_snapshot instead.

            Returns image_path. To see what the picture shows, call
            analyze_image with that image_path.

            Args:
                ref: Element ref from browser_snapshot (e.g. 'g2e5') to picture
                     only that element. Leave empty for the whole visible page.
            """
            try:
                with mixin._reading("browser_screenshot") as driver:
                    shot = driver.screenshot(ref or None)
                path = _save_screenshot(shot.png)
                mixin._let_read(path)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_screenshot(%s) failed: %s", ref, e)
                return {"status": "error", "error": fail(e)}
            what = f"element {ref}" if ref else "the visible page"
            return {
                "browser": driver.where,
                "status": "success",
                "message": f"Took a picture of {what} at {shot.url}.",
                "image_path": path,
                "width": shot.width,
                "height": shot.height,
                "next_step": "To see what it shows, call analyze_image with this image_path.",
            }

        @tool(atomic=True, registry=registry)
        def browser_logs(
            kind: str = "console",
            errors_only: bool = False,
            contains: str = "",
            new_only: bool = False,
        ) -> str:
            """Read the browser pane page's console messages or network requests.

            Use to find out why a page misbehaves: script errors, failed
            requests, bad status codes.

            Args:
                kind: 'console' for messages and script errors, or 'network'
                      for requests and their status codes
                errors_only: Only errors: console errors, failed requests,
                             and responses with status 400 or higher
                contains: Only lines containing this text (case-insensitive)
                new_only: Only what arrived since your last browser_logs call
            """
            if kind not in ("console", "network"):
                return "Error: kind must be 'console' or 'network'."
            try:
                with mixin._reading("browser_logs") as driver:
                    res = driver.logs(
                        kind,
                        errors_only=errors_only,
                        pattern=contains,
                        new_only=new_only,
                    )
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_logs(%s) failed: %s", kind, e)
                return fail(e)
            return f"{driver.where}\n" + _render_logs(kind, res, errors_only, contains)


def _save_screenshot(png: bytes) -> str:
    """Write a pane screenshot where the Agent UI can show it, and return the path."""
    from datetime import datetime

    from gaia.config import gaia_home

    folder = gaia_home() / "screenshots" / "browser-pane"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"pane_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
    path.write_bytes(png)
    return str(path)


def _render_logs(kind: str, res: dict, errors_only: bool, contains: str) -> str:
    """One line per console message or request, for the model."""
    entries = res.get("entries") or []
    noun = "console messages" if kind == "console" else "network requests"
    filters = [
        f
        for f in (
            "errors only" if errors_only else "",
            f'containing "{contains}"' if contains else "",
        )
        if f
    ]
    head = f"{len(entries)} {noun} on {res.get('url') or 'this page'}"
    if filters:
        head += f" ({', '.join(filters)})"
    lines = [head + ":" if entries else head + "."]
    for e in entries:
        if kind == "console":
            line = f"  [{e.get('level')}] {e.get('text')}"
            if e.get("source"):
                line += f"  ({e['source']})"
        else:
            status = e.get("status") or "FAILED"
            line = f"  {status} {e.get('method')} {e.get('url')}"
            if e.get("failure"):
                line += f" ({e['failure']})"
        lines.append(line)
    if res.get("dropped"):
        lines.append(f"  ... {res['dropped']} older entries were dropped.")
    return "\n".join(lines)
