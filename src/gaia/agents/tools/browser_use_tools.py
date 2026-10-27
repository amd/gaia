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

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Tuple

from gaia.agents.base.call_risk import RISK_NOTE_ARG
from gaia.agents.base.tool_grants import DEV_SERVER_TOOL, SITE_ARG
from gaia.browser.errors import BrowserError, PaneSignInByHand
from gaia.browser.snapshot import is_pick_ref
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
    "create account",
)


#: Pane tools that read or act on a page. Off loopback, each asks before it touches a site.
_PANE_SITE_TOOLS = frozenset(
    {
        "browser_open",
        "browser_snapshot",
        "browser_find",
        "browser_back",
        "browser_screenshot",
        "browser_logs",
        "browser_click",
        "browser_type",
        "browser_press",
    }
)

#: What browser_batch may run, and the ones that change the page without reading it back.
_BATCH_STEPS = (
    "browser_open",
    "browser_click",
    "browser_type",
    "browser_press",
    "browser_back",
    "browser_snapshot",
    "browser_screenshot",
)
_BATCH_ACTS = frozenset({"browser_click", "browser_type", "browser_press"})
MAX_BATCH_STEPS = 10


class _Refused(Exception):
    """A tool refusing its arguments; the message goes to the model as written."""


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
    #: (id(args), spec digest, already running) of the dev server the prompt showed.
    _dev_pending: Optional[Tuple[int, str, bool]] = None

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
        if tool_name == DEV_SERVER_TOOL:
            return self._dev_server_prompt(tool_args) or shown
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
        if origin:
            shown = {**shown, SITE_ARG: origin}
        label = self._irreversible_click_label(tool_name, tool_args)
        if label:
            # A one-time prompt: a site grant must never cover a click like this.
            shown = {
                **shown,
                RISK_NOTE_ARG: f'This clicks "{label}" on the page, which may not be undoable.',
            }
        return shown

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

    def _irreversible_click_label(
        self, tool_name: str, tool_args: Optional[dict]
    ) -> str:
        """The label of the control a click would press, when that label names an irreversible action."""
        if tool_name != "browser_click":
            return ""
        ref = str((tool_args or {}).get("ref") or "")
        if is_pick_ref(ref):
            label = self._pick_label(ref)
            if label is None:
                return "an element the user picked (its label could not be read)"
        else:
            label = self._element_label(ref)
        return label if self._looks_irreversible(label) else ""

    def _pick_label(self, ref: str) -> Optional[str]:
        """The live label of an element the user picked, or None when the pane can't say."""
        try:
            pane = self._pane()
            return pane.pick_label(ref) if pane is not None else ""
        except BrowserError as e:
            # Fails closed: the caller treats an unreadable label as irreversible.
            logger.warning("Could not read the label of picked element %s: %s", ref, e)
            return None

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
        if tool_name == DEV_SERVER_TOOL:
            pending = self._dev_pending
            # Starting a server asks, unless that exact one already runs.
            return (
                pending is not None and pending[0] == id(tool_args) and not pending[2]
            )
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
        """The site gate, plus the irreversible-click check headless has.

        A pane call asks before it reads or acts on a page off loopback;
        "Always" is a ``_site_scope`` grant on that origin, answered by the
        console before any prompt. A click on a control labelled with an
        irreversible action ("Delete", "Place order", "Create account") asks
        every time, on loopback too. Sign-in is not asked about: the pane
        refuses it.
        """
        if tool_name not in _PANE_SITE_TOOLS:
            return False
        origin = self._pane_site(pane, tool_name, tool_args)
        self._site_pending = (tool_name, origin)
        return origin is not None or bool(
            self._irreversible_click_label(tool_name, tool_args)
        )

    def _step_would_ask(self, tool_name: str, tool_args: dict) -> bool:
        """Whether running this call on its own would put a prompt in front of the user.

        Runs the same gate the agent runs before any tool, so a batch step
        that would ask is never run unasked. A call an earlier "always"
        already covers, or a run that approves everything, does not ask.
        """
        shows = getattr(self, "confirmation_args_for_call", None)
        shown = shows(tool_name, tool_args) if callable(shows) else tool_args
        gate = getattr(self, "_tool_requires_confirmation", None)
        if callable(gate):
            needs = gate(tool_name, tool_args)
        else:
            needs = self.browser_call_needs_confirmation(tool_name, tool_args)
        if not needs:
            return False
        console = getattr(self, "console", None)
        if console is None:
            return True
        return not (
            console.auto_approve_confirmations_enabled()
            or console.call_is_granted(tool_name, shown)
        )

    # ------------------------------------------------------------- dev servers

    def _dev_allowed(self, folder: Path) -> bool:
        """Whether ``folder`` is inside the folders this chat may use. No sandbox, no folder."""
        from gaia.agents.tools.search_scope import path_validator_of

        validator = path_validator_of(self)
        return validator is not None and validator.is_path_allowed(
            str(folder), prompt_user=False
        )

    def _dev_project(self, project: str) -> Path:
        """The folder whose ``.gaia/launch.json`` a dev_server call reads."""
        from gaia.agents.tools.search_scope import search_roots
        from gaia.browser.dev_servers import (
            LAUNCH_FILE,
            LaunchConfigError,
            find_projects,
        )

        if project:
            return Path(project).expanduser()
        roots = search_roots(self)
        found = find_projects(roots)
        if len(found) == 1:
            return found[0]
        if found:
            raise LaunchConfigError(
                f"More than one project has a {LAUNCH_FILE}: "
                + ", ".join(str(f) for f in found)
                + ". Pass project= with the one you mean."
            )
        raise LaunchConfigError(
            f"No {LAUNCH_FILE} in the folders this chat uses ("
            + ", ".join(str(r) for r in roots)
            + f"). Pass project= with the project folder. If it has no {LAUNCH_FILE}, "
            "write one there with write_file first."
        )

    def _dev_spec(self, name: str, project: str):
        from gaia.browser.dev_servers import spec_named

        return spec_named(self._dev_project(project), name, allowed=self._dev_allowed)

    def _dev_server_prompt(self, tool_args) -> Optional[Dict[str, Any]]:
        """What the prompt shows for ``dev_server(action="start")``: the exact command, folder and port.

        Display and grant only. The tool body resolves the spec again and runs
        it only when its digest is the one shown here, so an edit between the
        prompt and the run is refused rather than run unseen.
        """
        from gaia.browser.dev_servers import LaunchConfigError

        self._dev_pending = None
        args = tool_args if isinstance(tool_args, dict) else {}
        if args.get("action") != "start":
            return None
        try:
            pane = self._pane()
            if pane is None:
                return None
            spec = self._dev_spec(
                str(args.get("name") or ""), str(args.get("project") or "")
            )
            running = any(_same_server(s, spec) for s in pane.dev_servers())
        except (LaunchConfigError, BrowserError) as e:
            # The tool body raises the same error before it runs anything.
            logger.info("dev_server start not prompted: %s", e)
            return None
        self._dev_pending = (id(tool_args), spec.digest, running)
        return spec.prompt_args()

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
            if isinstance(e, _Refused):
                return str(e)
            msg = str(e) if isinstance(e, BrowserError) else f"Browser error: {e}"
            where = getattr(mixin._browser_driver, "where", None)
            return f"{where}\n{msg}" if where else msg

        def _answer(name: str, run, *args, **kwargs):
            try:
                return run(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("%s%s failed: %s", name, args, e)
                return _fail(e)

        def _seen(snap: dict) -> None:
            mixin._note_location(snap)
            mixin._note_elements(snap)

        def _open(url: str) -> str:
            pane = mixin._pane()
            if pane is None:
                bad = mixin._check_navigable(url)
                if bad:
                    raise _Refused(bad)
                # Restore whenever this origin has a stored session and this
                # run has not already loaded it — NOT only on the first
                # browser call. The common trajectory is "read something,
                # then go to the site that needs the account"; gating on a
                # cold driver meant the saved sign-in was skipped exactly
                # then, and the agent landed on a login screen.
                if not mixin.browser_origin_is_authenticated(url):
                    mixin._restore_session_for(url)
            elif not url.startswith(("http://", "https://")):
                raise _Refused(
                    f"Error: invalid URL {url!r}. It must start with http:// "
                    "or https://."
                )
            with mixin._reading("browser_open") as driver:
                snap = driver.goto(url)
            _seen(snap)
            return f"{driver.where}\n{render(snap)}"

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
            return _answer("browser_open", _open, url)

        def _snapshot() -> str:
            with mixin._reading("browser_snapshot") as driver:
                snap = driver.snapshot()
            _seen(snap)
            return f"{driver.where}\n{render(snap)}"

        @tool(atomic=True, registry=shared)
        def browser_snapshot() -> str:
            """Read the page open in the browser: its title, URL, text and interactive elements.

            In GAIA desktop this is the page in the browser pane beside the
            chat. Call it again after anything that changes the page, or when
            a ref from an earlier snapshot no longer works. Refs are only valid
            until the next snapshot.
            """
            return _answer("browser_snapshot", _snapshot)

        def _click(ref: str) -> str:
            with mixin._reading("browser_click") as driver:
                snap = driver.click(ref)
            _seen(snap)
            return f"{driver.where}\nClicked {ref}.\n\n" + render(snap)

        @tool(atomic=True, registry=shared)
        def browser_click(ref: str) -> str:
            """Click an element on the current page.

            Args:
                ref: Element ref from browser_snapshot (e.g. 'e7'), or 'p1'
                     for an element the user picked. Not a CSS selector
            """
            return _answer("browser_click", _click, ref)

        def _type(ref: str, text: str, press_enter: bool = False) -> str:
            with mixin._reading("browser_type") as driver:
                snap = driver.type_text(ref, text, press_enter=press_enter)
            _seen(snap)
            suffix = " and pressed Enter" if press_enter else ""
            return f"{driver.where}\nTyped into {ref}{suffix}.\n\n" + render(snap)

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
            return _answer("browser_type", _type, ref, text, press_enter)

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
                pane = mixin._pane()
                if pane is not None:
                    return f"{pane.where}\n{PaneSignInByHand()}"
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

        def _back() -> str:
            with mixin._reading("browser_back") as driver:
                snap = driver.back()
            _seen(snap)
            return f"{driver.where}\nWent back.\n\n" + render(snap)

        @tool(atomic=True, registry=shared)
        def browser_back() -> str:
            """Go back to the previous page, or close a tab and return to its opener.

            Use when a path turned out to be a dead end, or after reading a
            page that opened in a new tab and you need the one you came from.

            Returns the interactive elements of the page you land on.
            """
            return _answer("browser_back", _back)

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
            self._register_pane_tools(tool, here, _fail, _answer, _seen)
            self._register_dev_server_tool(tool, here, _fail, _seen)

    def _register_pane_tools(self, tool, registry, fail, answer, seen) -> None:
        """Tools only the browser pane can serve, on this agent alone."""
        from gaia.browser.pane_driver import VIEWPORT_PRESETS
        from gaia.browser.snapshot import render

        mixin = self

        def _picture(ref: str = "") -> Dict[str, Any]:
            with mixin._reading("browser_screenshot") as driver:
                shot = driver.screenshot(ref or None)
            path = _save_screenshot(shot.png)
            mixin._let_read(path)
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
                return _picture(ref)
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("browser_screenshot(%s) failed: %s", ref, e)
                return {"status": "error", "error": fail(e)}

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

        def _press(key: str, ref: str = "") -> str:
            with mixin._reading("browser_press") as driver:
                snap = driver.press(key, ref or None)
            seen(snap)
            on = f" on {ref}" if ref else ""
            return f"{driver.where}\nPressed {key}{on}.\n\n" + render(snap)

        @tool(atomic=True, registry=registry)
        def browser_press(key: str, ref: str = "") -> str:
            """Press one key on the page in the browser pane.

            Use Escape to close a dialog or menu, Enter to submit, Tab to go
            to the next field, PageDown or End to scroll down.

            Args:
                key: One of Enter, Escape, Tab, Backspace, Space, PageDown,
                     PageUp, Home, End, ArrowUp, ArrowDown, ArrowLeft, ArrowRight
                ref: Element ref to press the key on (e.g. 'g2e5'). Leave
                     empty for whatever has focus.
            """
            return answer("browser_press", _press, key, ref)

        @tool(atomic=True, registry=registry)
        def browser_viewport(
            preset: str = "", width: int = 0, height: int = 0, color_scheme: str = ""
        ) -> str:
            """Show the browser pane's page at a phone, tablet or desktop size, or in dark mode.

            Use to check how a page looks on other screens. If the user chose
            a size in the pane's Responsive menu, theirs stays.

            Args:
                preset: 'mobile', 'tablet', 'desktop', or 'fill' to fit the pane again
                width: Exact width in pixels instead of a preset (give height too)
                height: Exact height in pixels instead of a preset
                color_scheme: 'dark' or 'light'. Leave empty for the system setting.
            """
            if preset and preset not in (*VIEWPORT_PRESETS, "fill"):
                return "Error: preset must be 'mobile', 'tablet', 'desktop' or 'fill'."
            if color_scheme not in ("", "dark", "light"):
                return "Error: color_scheme must be 'dark', 'light' or empty."
            if (width or height) and not (
                200 <= width <= 4000 and 200 <= height <= 4000
            ):
                return (
                    "Error: give both width and height, each from 200 to 4000 pixels."
                )
            if width:
                size = {"width": width, "height": height, "mobile": width < 768}
            else:
                size = VIEWPORT_PRESETS.get(preset)

            def _set() -> str:
                driver = mixin._ensure_driver()
                res = driver.viewport(size, color_scheme or None)
                return f"{driver.where}\n" + _describe_viewport(res)

            return answer("browser_viewport", _set)

        @tool(atomic=True, registry=registry)
        def browser_tabs(switch_to: str = "") -> str:
            """List the tabs open in the browser pane, or switch to one.

            The browser tools read and act in the tab that is showing.

            Args:
                switch_to: A tab id from the list (e.g. 'tab-2') to show that
                           tab. Leave empty to only list them.
            """

            def _list() -> str:
                driver = mixin._ensure_driver()
                res = driver.select_tab(switch_to) if switch_to else driver.tabs()
                return f"{driver.where}\n" + _render_tabs(res, switch_to)

            return answer("browser_tabs", _list)

        def _act_step(driver, name: str, args: Dict[str, Any]) -> str:
            ref = str(args.get("ref") or "")
            if name == "browser_click":
                driver.click(ref, read=False)
                label = mixin._element_label(ref)
                return f'Clicked {ref} ("{label}").' if label else f"Clicked {ref}."
            if name == "browser_type":
                enter = bool(args.get("press_enter"))
                driver.type_text(ref, str(args["text"]), press_enter=enter, read=False)
                return f"Typed into {ref}" + (" and pressed Enter." if enter else ".")
            driver.press(str(args["key"]), ref or None, read=False)
            return f"Pressed {args['key']}" + (f" on {ref}." if ref else ".")

        def _read_step(driver, name: str, args: Dict[str, Any]):
            if name == "browser_open":
                url = str(args["url"])
                if not url.startswith(("http://", "https://")):
                    raise _Refused(f"invalid URL {url!r}; it must start with http://")
                snap = driver.goto(url)
                return f"Opened {snap.get('url') or url}.", snap
            if name == "browser_back":
                return "Went back.", driver.back()
            if name == "browser_snapshot":
                return "Read the page.", driver.snapshot()
            shot = driver.screenshot(str(args.get("ref") or "") or None)
            path = _save_screenshot(shot.png)
            mixin._let_read(path)
            return (
                f"Took a picture: image_path {path}. To see it, call "
                "analyze_image with this image_path.",
                None,
            )

        @tool(atomic=True, timeout=300, registry=registry)
        def browser_batch(steps: List[dict]) -> str:
            """Run several browser steps in one call, in order, in the browser pane.

            Use for a known sequence, like filling in a form. Steps can use
            browser_open, browser_click, browser_type, browser_press,
            browser_back, browser_snapshot and browser_screenshot. The batch
            stops at the first step that fails, and before any step that needs
            the user's approval: call that step on its own and GAIA asks.

            Args:
                steps: Steps in order, each a tool name and its arguments, e.g.
                    [{"tool": "browser_type", "ref": "g2e3", "text": "Ada"},
                    {"tool": "browser_click", "ref": "g2e7"}]
            """
            try:
                plan = _parse_batch(steps)
            except ValueError as e:
                return f"Error: {e}"
            try:
                driver = mixin._ensure_driver()
            except Exception as e:  # noqa: BLE001 — returned to the model
                return fail(e)
            done: List[str] = []
            stop = ""
            snap = None
            unread = False
            approved: List[str] = []
            for n, (name, args) in enumerate(plan, 1):
                if mixin._step_would_ask(name, args):
                    stop = (
                        f"Stopped before step {n} ({name}): it needs the user's "
                        f"approval. Call {_call_text(name, args)} on its own, and "
                        "GAIA will ask the user."
                    )
                    break
                approved = mixin._take_site_approval(name)
                try:
                    with driver.scoped(approved):
                        if name in _BATCH_ACTS:
                            done.append(f"{n}. {_act_step(driver, name, args)}")
                            unread = True
                            continue
                        line, read = _read_step(driver, name, args)
                except Exception as e:  # noqa: BLE001 — reported in the result
                    logger.error("browser_batch step %d (%s) failed: %s", n, name, e)
                    stop = f"Step {n} ({name}) failed, so the batch stopped: {e}"
                    break
                done.append(f"{n}. {line}")
                if read is not None:
                    snap, unread = read, False
                    seen(snap)
            if unread:
                try:
                    with driver.scoped(approved):
                        snap = driver.snapshot()
                    seen(snap)
                except Exception as e:  # noqa: BLE001 — reported in the result
                    done.append(f"Could not read the page afterwards: {e}")
            lines = [driver.where, f"Ran {len(done)} of {len(plan)} steps:", *done]
            if stop:
                lines.append(stop)
            if snap is not None:
                lines += ["", render(snap)]
            return "\n".join(lines)

    def _register_dev_server_tool(self, tool, registry, fail, seen) -> None:
        """dev_server, on this agent alone: only GAIA desktop runs dev servers."""
        from gaia.browser.dev_servers import MAX_READY_TIMEOUT_S, describe, pick_running
        from gaia.browser.snapshot import render

        mixin = self

        def _start(driver, name: str, project: str) -> str:
            pending, mixin._dev_pending = mixin._dev_pending, None
            spec = mixin._dev_spec(name, project)
            if pending is None or pending[1] != spec.digest:
                raise _Refused(
                    "Error: not started. The server's command changed after the user "
                    "approved it, or it was never approved. Call dev_server again and "
                    "GAIA will ask the user."
                )
            res = driver.dev_server_ensure(spec.key, spec.to_wire())
            snap = driver.goto(spec.url)
            seen(snap)
            how = (
                "was already running"
                if res["reused"]
                else f"started in {res['ms'] / 1000:.1f} s"
            )
            return (
                f'{driver.where}\nDev server "{spec.name}" (`{spec.command_line}`) {how} '
                f"on port {spec.port}. The browser pane shows {spec.url}.\n\n{render(snap)}"
            )

        def _running(driver, name: str) -> Dict[str, Any]:
            servers = driver.dev_servers()
            server = pick_running(servers, name)
            if server is None:
                listed = ", ".join(s["name"] for s in servers) or "none"
                raise _Refused(
                    f"Error: no single dev server matches name={name!r}. GAIA's dev "
                    f"servers: {listed}. Pass name= with one of them."
                )
            return server

        def _logs(driver, name: str, errors_only: bool, contains: str) -> str:
            server = _running(driver, name)
            res = driver.dev_server_logs(
                server["key"], errors_only=errors_only, pattern=contains, limit=40
            )
            lines = res.get("lines") or []
            what = "error line(s)" if errors_only else "line(s)"
            if contains:
                what += f' containing "{contains}"'
            head = f'Dev server "{server["name"]}" ({server["status"]}): {res.get("matched", 0)} {what}'
            if not lines:
                return f"{driver.where}\n{head}."
            shown = (
                f", the last {len(lines)}" if res.get("matched", 0) > len(lines) else ""
            )
            return f"{driver.where}\n{head}{shown}:\n" + "\n".join(
                f"  {line}" for line in lines
            )

        def _run(
            action: str, name: str, project: str, errors_only: bool, contains: str
        ):
            pane = mixin._pane()
            if pane is None:
                raise _Refused(
                    "Error: dev servers run only in a chat open in GAIA desktop. "
                    "Use run_shell_command instead."
                )
            # Results name the pane, failures included.
            mixin._ensure_driver()
            if action == "start":
                return _start(pane, name, project)
            if action == "logs":
                return _logs(pane, name, errors_only, contains)
            if action == "stop":
                server = pane.dev_server_stop(_running(pane, name)["key"])
                return f'{pane.where}\nStopped dev server "{server["name"]}"; port {server["port"]} is free.'
            servers = pane.dev_servers()
            if not servers:
                return f"{pane.where}\nNo dev servers. Start one with dev_server(action='start')."
            return f"{pane.where}\nDev servers:\n" + "\n".join(
                f"  {describe(s)}" for s in servers
            )

        @tool(atomic=True, timeout=MAX_READY_TIMEOUT_S + 60, registry=registry)
        def dev_server(
            action: str,
            name: str = "",
            project: str = "",
            errors_only: bool = False,
            contains: str = "",
        ) -> str:
            """Run a project's dev server and show it in the browser pane; or list, stop, or read its logs.

            Use this, not run_shell_command, to start any dev or web server:
            it keeps running after the reply. Servers are defined in
            <project>/.gaia/launch.json. If it is missing,
            write it with write_file first, like:
            {"version": "0.0.1", "configurations": [{"name": "web",
            "runtimeExecutable": "npm", "runtimeArgs": ["run", "dev"], "port": 5173}]}
            Starting asks the user first. A server already running is reused.

            Args:
                action: 'start' (runs it and opens it in the pane), 'list',
                        'logs', or 'stop'
                name: The server's name in launch.json, like 'web'. Needed when
                      it has more than one.
                project: Folder holding .gaia/launch.json. Leave empty for the
                         current project.
                errors_only: For logs: only error lines
                contains: For logs: only lines containing this text
            """
            from gaia.browser.dev_servers import LaunchConfigError

            if action not in ("start", "list", "logs", "stop"):
                return "Error: action must be 'start', 'list', 'logs' or 'stop'."
            try:
                return _run(action, name, project, errors_only, contains)
            except LaunchConfigError as e:
                return f"Error: {e}"
            except Exception as e:  # noqa: BLE001 — returned to the model
                logger.error("dev_server(%s, %s) failed: %s", action, name, e)
                return fail(e)


def _same_server(state: Dict[str, Any], spec) -> bool:
    """Whether main already runs exactly this spec, so starting it runs nothing new."""
    return (
        state.get("key") == spec.key
        and state.get("status") == "ready"
        and state.get("argv") == list(spec.argv)
        and state.get("cwd") == str(spec.cwd)
        and state.get("port") == spec.port
        and (state.get("env") or {}) == dict(spec.env)
    )


def _parse_batch(steps: Any) -> List[Tuple[str, Dict[str, Any]]]:
    """``[(tool, args), ...]`` from the model's steps; ValueError names what is wrong."""
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except ValueError as e:
            raise ValueError(f"steps is not a JSON list ({e}).") from e
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list of steps.")
    if len(steps) > MAX_BATCH_STEPS:
        raise ValueError(f"a batch runs at most {MAX_BATCH_STEPS} steps.")
    plan = []
    for n, step in enumerate(steps, 1):
        if not isinstance(step, dict) or "tool" not in step:
            raise ValueError(
                f'step {n} must be like {{"tool": "browser_click", "ref": "g2e5"}}.'
            )
        args = {k: v for k, v in step.items() if k not in ("tool", "args")}
        if isinstance(step.get("args"), dict):
            args.update(step["args"])
        name = str(step["tool"])
        if name not in _BATCH_STEPS:
            raise ValueError(
                f"step {n} uses {name}; a batch can use {', '.join(_BATCH_STEPS)}."
            )
        reserved = sorted(k for k in args if k.startswith("_"))
        if reserved:
            raise ValueError(
                f"step {n} has arguments GAIA sets itself: {', '.join(reserved)}."
            )
        needed = {
            "browser_open": ("url",),
            "browser_click": ("ref",),
            "browser_type": ("ref", "text"),
            "browser_press": ("key",),
        }.get(name, ())
        missing = [k for k in needed if k not in args]
        if missing:
            raise ValueError(f"step {n} ({name}) needs {', '.join(missing)}.")
        plan.append((name, args))
    return plan


def _call_text(name: str, args: Dict[str, Any]) -> str:
    return f"{name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"


def _describe_viewport(res: dict) -> str:
    """What browser_viewport did, for the model."""
    v = res.get("viewport") or {}
    size = v.get("size")
    shown = f"{size['width']}x{size['height']}" if size else "the full pane width"
    scheme = v.get("colorScheme")
    look = f"{shown}, {scheme} mode" if scheme else shown
    if not res.get("applied"):
        return (
            f"Not changed. The user picked {look} in the pane's Responsive menu, "
            "and the user's choice wins, so the page still shows at that size. "
            "Tell the user, and ask them to pick Fill pane there if they want "
            "your size."
        )
    return f"The page now shows at {look}. Call browser_screenshot to see it."


def _render_tabs(res: dict, switched_to: str) -> str:
    tabs = res.get("tabs") or []
    lines = [f"Switched to {switched_to}." if switched_to else f"{len(tabs)} tabs:"]
    for t in tabs:
        mark = " (showing)" if t.get("active") else ""
        paused = (
            " (the user has control; GAIA is paused there)"
            if t.get("control") == "user"
            else ""
        )
        lines.append(
            f"  {t.get('id')}{mark}{paused} {t.get('title') or 'New tab'}  {t.get('url') or ''}".rstrip()
        )
    return "\n".join(lines)


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
