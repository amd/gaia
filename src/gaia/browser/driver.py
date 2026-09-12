# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Thread-confined Playwright driver with a persistent browser.

Two facts about the host decide this design.

**Every tool body runs in a fresh daemon thread.** ``Agent._call_tool_bounded``
spawns one per call, and Playwright's sync objects are thread-affine — created
on one thread, unusable from another. So Playwright lives on a single
long-lived worker thread of its own and every operation is marshalled to it.

**The browser stays open between calls.** A cold Chromium launch is 1–3 s; the
CDP command it carries is 1–10 ms. Launching per tool call would make the
driver, rather than the model, the thing the user waits for. The browser is
launched lazily on first use and reused for the rest of the session.
"""

from __future__ import annotations

import importlib.util
import os
import queue
import threading
from typing import Any, Callable, Dict, List, Optional

from gaia.browser.errors import (
    BrowserLaunchFailed,
    BrowserNotInstalled,
    BrowserNotStarted,
    ElementNotFound,
    InteractionFailed,
    LoginTimedOut,
    NavigationBlocked,
    NavigationFailed,
)
from gaia.browser.snapshot import _SNAPSHOT_JS, ref_selector, snapshot_args
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Per-operation ceiling. Generous enough for a slow page, short enough that a
#: wedged browser surfaces as an error instead of hanging the agent loop.
DEFAULT_OP_TIMEOUT_S = 45.0

#: Navigation budget. Distinct from the op timeout: a page load legitimately
#: takes longer than a click.
DEFAULT_NAV_TIMEOUT_MS = 30_000

#: How long to let an action-triggered navigation commit before snapshotting.
#: Short: it is paid in full by every action that navigates nowhere, and the
#: model's own step costs tens of seconds either way.
NAV_SETTLE_S = 1.5

#: How long a user gets to finish signing in before browser_login gives up.
DEFAULT_LOGIN_TIMEOUT_S = 300.0

#: Never call a sign-in complete before this. Analytics and CSRF cookies land
#: in the first second on almost every login page.
MIN_DWELL_S = 3.0

#: Extra patience when no password field was ever shown (passkey, live SSO, or
#: an email-first page still on step one).
NO_PASSWORD_DWELL_S = 10.0

_SHUTDOWN = object()


def _headless_default() -> bool:
    """Headless unless the host says otherwise.

    Login needs a visible window, so ``browser_login`` overrides this per
    session; everything else runs headless because it is faster and does not
    steal focus.
    """
    raw = os.getenv("GAIA_BROWSER_HEADLESS")
    if raw is None:
        return True
    return raw.strip().lower() in ("1", "true", "yes", "on")


class PlaywrightDriver:
    """A persistent Chromium, driven from one dedicated thread.

    Not thread-safe by accident — it is thread-safe *by construction*: callers
    never touch a Playwright object, they submit a callable that runs on the
    worker thread and get the return value back.
    """

    def __init__(
        self,
        *,
        headless: Optional[bool] = None,
        nav_timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
        op_timeout_s: float = DEFAULT_OP_TIMEOUT_S,
        storage_state: Optional[Dict[str, Any]] = None,
        user_agent: Optional[str] = None,
        allow_navigation: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self._headless = _headless_default() if headless is None else headless
        self._nav_timeout_ms = nav_timeout_ms
        self._op_timeout_s = op_timeout_s
        self._storage_state = storage_state
        self._user_agent = user_agent
        self._allow_navigation = allow_navigation
        #: URL the guard most recently refused. Read when a navigation fails so
        #: the caller is told it was blocked, not that the site was slow.
        self._last_blocked: Optional[str] = None

        self._jobs: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None
        self._lock = threading.Lock()

        # Owned by the worker thread only.
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # ---------------------------------------------------------------- lifecycle

    @property
    def started(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Launch the browser. Idempotent; blocks until it is usable."""
        with self._lock:
            if self.started:
                return
            self._ready.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run, name="gaia-browser", daemon=True
            )
            self._thread.start()

        # Launch budget is deliberately larger than an op: a first run may be
        # unpacking the browser bundle.
        if not self._ready.wait(timeout=90.0):
            raise BrowserLaunchFailed("browser did not become ready within 90s")
        if self._start_error is not None:
            raise self._start_error

    def close(self) -> None:
        """Shut the browser down and join the worker thread."""
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._thread = None
                return
            self._jobs.put(_SHUTDOWN)
        thread.join(timeout=20.0)
        if thread.is_alive():
            logger.warning("Browser worker did not exit within 20s; abandoning it.")
        with self._lock:
            self._thread = None

    def _run(self) -> None:
        """Worker thread: owns every Playwright object for its whole life."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            self._start_error = BrowserNotInstalled(str(e))
            self._ready.set()
            return

        pw = None
        try:
            pw = sync_playwright().start()
            self._pw = pw
            try:
                self._browser = pw.chromium.launch(headless=self._headless)
            except Exception as e:  # noqa: BLE001 — re-raised as an actionable error
                msg = str(e)
                if "Executable doesn't exist" in msg or "playwright install" in msg:
                    self._start_error = BrowserNotInstalled(msg.split("\n")[0])
                else:
                    self._start_error = BrowserLaunchFailed(msg.split("\n")[0])
                self._ready.set()
                return

            ctx_kwargs: Dict[str, Any] = {}
            if self._storage_state:
                ctx_kwargs["storage_state"] = self._storage_state
            if self._user_agent:
                ctx_kwargs["user_agent"] = self._user_agent
            self._context = self._browser.new_context(**ctx_kwargs)
            self._context.set_default_timeout(self._nav_timeout_ms)
            self._install_navigation_guard()
            self._page = self._context.new_page()

            logger.info(
                "Browser started (headless=%s, session=%s)",
                self._headless,
                "restored" if self._storage_state else "fresh",
            )
            self._ready.set()
            self._pump()
        except BaseException as e:  # noqa: BLE001 — surfaced to start()/submit()
            if not self._ready.is_set():
                self._start_error = BrowserLaunchFailed(str(e).split("\n")[0])
                self._ready.set()
            else:
                logger.error("Browser worker died: %s", e)
        finally:
            for obj, what in (
                (self._context, "context"),
                (self._browser, "browser"),
                (pw, "playwright"),
            ):
                if obj is None:
                    continue
                try:
                    obj.stop() if what == "playwright" else obj.close()
                except Exception as e:  # noqa: BLE001 — teardown is best-effort
                    logger.debug("Browser %s teardown: %s", what, e)
            self._pw = self._browser = self._context = self._page = None
            logger.info("Browser stopped")

    def _pump(self) -> None:
        """Run submitted jobs until shutdown."""
        while True:
            job = self._jobs.get()
            if job is _SHUTDOWN:
                return
            fn, result_q = job
            try:
                result_q.put(("ok", fn()))
            except BaseException as e:  # noqa: BLE001 — re-raised in the caller
                result_q.put(("err", e))

    def _submit(self, fn: Callable[[], Any], *, timeout: Optional[float] = None) -> Any:
        """Run ``fn`` on the worker thread and return its value."""
        if not self.started:
            raise BrowserNotStarted()
        result_q: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        self._jobs.put((fn, result_q))
        try:
            kind, payload = result_q.get(timeout=timeout or self._op_timeout_s)
        except queue.Empty:
            raise InteractionFailed(
                "page",
                "complete the operation",
                f"the browser did not respond within {timeout or self._op_timeout_s:g}s",
            ) from None
        if kind == "err":
            raise payload
        return payload

    # ------------------------------------------------------------------- actions

    def goto(self, url: str) -> Dict[str, Any]:
        """Navigate and return a fresh snapshot."""

        def _go() -> Dict[str, Any]:
            self._last_blocked = None
            try:
                self._page.goto(url, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001 — re-raised with the URL
                # A blocked hop surfaces as an abort or a timeout, neither of
                # which says why. Report the refusal instead of the symptom.
                if self._last_blocked:
                    raise NavigationBlocked(url, self._last_blocked) from e
                raise NavigationFailed(url, str(e).split("\n")[0]) from e
            self._assert_landed_somewhere_allowed(url)
            self._settle()
            return self._snapshot()

        return self._submit(_go, timeout=self._nav_timeout_ms / 1000 + 15)

    def snapshot(self) -> Dict[str, Any]:
        return self._submit(self._snapshot)

    def click(self, ref: str) -> Dict[str, Any]:
        selector = ref_selector(ref)

        def _click() -> Dict[str, Any]:
            loc = self._page.locator(selector)
            if loc.count() == 0:
                raise ElementNotFound(ref)
            prev_url = self._page.url
            try:
                # Playwright auto-waits for actionability and scrolls into view,
                # which is most of what makes a hand-rolled driver flaky.
                loc.first.click(timeout=self._nav_timeout_ms)
            except Exception as e:  # noqa: BLE001 — re-raised with the ref
                raise InteractionFailed(ref, "click", str(e).split("\n")[0]) from e
            self._settle_after_action(prev_url)
            self._assert_landed_somewhere_allowed(self._page.url)
            return self._snapshot()

        return self._submit(_click)

    def type_text(
        self, ref: str, text: str, *, press_enter: bool = False
    ) -> Dict[str, Any]:
        """Fill a text field, or pick an option when the target is a ``<select>``."""
        selector = ref_selector(ref)

        def _type() -> Dict[str, Any]:
            loc = self._page.locator(selector)
            if loc.count() == 0:
                raise ElementNotFound(ref)
            target = loc.first
            prev_url = self._page.url
            tag = (target.evaluate("(el) => el.tagName.toLowerCase()") or "").strip()
            try:
                if tag == "select":
                    target.select_option(label=text)
                else:
                    target.fill(text, timeout=self._nav_timeout_ms)
                    if press_enter:
                        # focus() + keyboard, rather than locator.press():
                        # press re-runs the full actionability check, which a
                        # live TUI run stalled on for its whole 30s budget on a
                        # field the next attempt typed into fine (the box had
                        # opened its autocomplete, and the machine was busy
                        # running inference). focus() is cheap and does not
                        # wait on actionability.
                        #
                        # The explicit focus() is load-bearing: relying on
                        # fill()'s focus and pressing at page level submitted
                        # nothing on Wikipedia, because the autocomplete
                        # re-renders the widget between the two calls.
                        target.focus(timeout=self._nav_timeout_ms)
                        self._page.keyboard.press("Enter")
            except Exception as e:  # noqa: BLE001 — re-raised with the ref
                action = "select an option in" if tag == "select" else "type into"
                raise InteractionFailed(ref, action, str(e).split("\n")[0]) from e
            self._settle_after_action(prev_url)
            self._assert_landed_somewhere_allowed(self._page.url)
            return self._snapshot()

        return self._submit(_type)

    def current_url(self) -> str:
        return self._submit(lambda: self._page.url)

    def origin_has_cookies(self, origin: str) -> bool:
        """Whether the live context holds a cookie that covers ``origin``.

        Sign-in state belongs to the browser context, not to the URL the user
        signed in at: after logging in at ``accounts.google.com`` the same
        context is authenticated for ``mail.google.com``. Asking the context
        is the only way to see that; comparing against the login origin misses
        every identity-provider split, which is most real sign-ins.
        """

        def _check() -> bool:
            from urllib.parse import urlparse

            host = (urlparse(origin).hostname or "").lower()
            if not host:
                return False
            for c in self._context.cookies():
                domain = str(c.get("domain") or "").lstrip(".").lower()
                if not domain:
                    continue
                if host == domain or host.endswith("." + domain):
                    return True
            return False

        return self._submit(_check)

    def cookies(self) -> List[Dict[str, Any]]:
        """Cookies held by the live context."""
        return self._submit(self._read_cookies)

    def storage_state(self) -> Dict[str, Any]:
        """Cookies + localStorage for the live context."""
        return self._submit(self._read_storage_state)

    def wait_for_login(
        self,
        url: str,
        *,
        timeout_s: float = DEFAULT_LOGIN_TIMEOUT_S,
        poll_s: float = 1.0,
    ) -> Dict[str, Any]:
        """Open ``url`` and block until the user has signed in.

        Sign-in is inferred, and the inference has to survive **email-first**
        flows (Google, Microsoft): step one asks for an address and shows no
        password field at all, while analytics and CSRF cookies land in the
        first second. "No password visible plus a new cookie" would call that
        a success about a second in and persist a logged-out session.

        So the signal is a *transition*, not a state: a password field has to
        have been seen and then gone, together with a navigation or a new
        cookie. Flows that never show one (passkey, an already-open SSO
        session) fall back to requiring both signals plus a longer dwell.
        Nothing is concluded inside ``MIN_DWELL_S`` either way.

        Conservative on purpose: a false negative costs the user a retry, a
        false positive saves a session that is not one and then fails
        confusingly on the next run.
        """

        def _login() -> Dict[str, Any]:
            import time as _time

            try:
                self._page.goto(url, wait_until="domcontentloaded")
            except Exception as e:  # noqa: BLE001 — re-raised with the URL
                raise NavigationFailed(url, str(e).split("\n")[0]) from e

            start_url = self._page.url
            cookies_before = {
                (c.get("name"), c.get("domain")) for c in self._context.cookies()
            }
            started = _time.monotonic()
            deadline = started + timeout_s
            saw_password = False

            while _time.monotonic() < deadline:
                _time.sleep(poll_s)
                try:
                    has_password = (
                        self._page.locator("input[type=password]:visible").count() > 0
                    )
                    now_url = self._page.url
                    cookies_now = {
                        (c.get("name"), c.get("domain"))
                        for c in self._context.cookies()
                    }
                except Exception:  # noqa: BLE001 — mid-navigation; try next poll
                    continue

                if has_password:
                    saw_password = True
                    continue

                elapsed = _time.monotonic() - started
                if elapsed < MIN_DWELL_S:
                    continue

                moved = now_url != start_url
                new_cookies = bool(cookies_now - cookies_before)
                if saw_password:
                    done = moved or new_cookies
                else:
                    # Never saw a password field: could be a passkey or an
                    # already-live SSO session, or could be an email-first
                    # page still on step one. Demand both signals and more
                    # time before believing it.
                    done = moved and new_cookies and elapsed >= NO_PASSWORD_DWELL_S

                if done:
                    self._settle()
                    snap = self._snapshot()
                    snap["signed_in"] = True
                    return snap

            raise LoginTimedOut(url, timeout_s)

        # The submit budget must outlast the human, not the machine.
        return self._submit(_login, timeout=timeout_s + 30)

    # ------------------------------------------------------------------ internals

    def _read_cookies(self) -> List[Dict[str, Any]]:
        return self._context.cookies()

    def _read_storage_state(self) -> Dict[str, Any]:
        # Named apart from ``self._storage_state`` (the seed state passed
        # to __init__): an instance attribute of the same name would
        # shadow this method and _submit would get a dict, not a callable.
        return self._context.storage_state()

    def _install_navigation_guard(self) -> None:
        """Screen every document navigation, not just the ones we initiate.

        Checking the URL at ``browser_open`` only covers the first hop. A link
        the model clicks, a redirect the server sends, and a script-driven
        navigation all reach the network without passing that check — so a page
        could walk the browser to a private address and hand its body back as
        page text. Routing at the context level screens all of them once,
        instead of bolting a check onto each entry point and missing the next.

        Only top-level document requests are screened: subresources inherit the
        document's origin, and validating every image would put a DNS lookup in
        front of every asset on the page.
        """
        if self._allow_navigation is None:
            return

        def _guard(route, request):
            try:
                if (
                    request.resource_type == "document"
                    and request.is_navigation_request()
                ):
                    if not self._allow_navigation(request.url):
                        logger.warning("Blocked navigation to %s", request.url)
                        self._last_blocked = request.url
                        route.abort("blockedbyclient")
                        return
            except Exception as e:  # noqa: BLE001 — never wedge the page on a guard bug
                logger.error("Navigation guard error for %s: %s", request.url, e)
            route.continue_()

        self._context.route("**/*", _guard)

    def _assert_landed_somewhere_allowed(self, requested: str) -> None:
        """Refuse to hand back a page that ended up off the public internet.

        The route guard sees the request the browser makes, but a server-side
        302 is followed inside the network stack without re-entering the
        handler — so a public URL can still land on a private one. This checks
        where the page actually ended up and blanks it rather than snapshotting
        it, so the body never reaches the model.

        It cannot un-send the request: for a redirect chain the private address
        was already contacted. What it prevents is the response becoming
        context, which is the part an attacker is after.
        """
        if self._allow_navigation is None:
            return
        try:
            landed = self._page.url
        except Exception:  # noqa: BLE001 — nothing to vouch for
            return
        if not landed or landed.startswith(("about:", "chrome-error:")):
            return
        if self._allow_navigation(landed):
            return
        try:
            self._page.goto("about:blank")
        except Exception as e:  # noqa: BLE001 — best-effort scrub
            logger.debug("Could not blank a blocked page: %s", e)
        raise NavigationBlocked(requested, landed)

    def _settle_after_action(self, prev_url: str) -> None:
        """Settle after a click/keypress that *might* navigate.

        An action can start a navigation that has not committed by the time the
        call returns. Snapshotting then captures the OLD document: the tool
        reports the previous URL and hands the model refs that are about to
        stop existing. That is not theoretical — it is what made a Wikipedia
        search look like it had submitted nothing, while the browser was in
        fact mid-navigation ("Execution context was destroyed").

        So wait briefly for the URL to change, then for the new document. An
        action that navigates nowhere costs the poll window and no more.
        """
        import time as _time

        deadline = _time.monotonic() + NAV_SETTLE_S
        while _time.monotonic() < deadline:
            try:
                if self._page.url != prev_url:
                    break
            except Exception:  # noqa: BLE001 — mid-swap; try again
                pass
            _time.sleep(0.05)

        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:  # noqa: BLE001 — already loaded, or never navigated
            pass
        self._settle()

    def _settle(self) -> None:
        """Give client-side rendering a beat to finish.

        ``networkidle`` is the honest signal but hangs forever on pages with
        long-polling or analytics beacons, so it is bounded and its timeout is
        not an error — a page that never goes idle is still readable.
        """
        try:
            self._page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:  # noqa: BLE001 — see docstring; this is not a failure
            pass

    def _snapshot(self) -> Dict[str, Any]:
        return self._page.evaluate(_SNAPSHOT_JS, snapshot_args())


def installed() -> bool:
    """True when the Playwright Python package is present.

    Uses ``find_spec`` rather than importing: this is called during tool
    registration on every agent build, and importing Playwright just to ask
    whether it exists would cost startup time on runs that never browse.

    Says nothing about the browser binary — that failure surfaces at launch
    with a message telling the user to run ``playwright install``.
    """
    return importlib.util.find_spec("playwright.sync_api") is not None


__all__: List[str] = ["PlaywrightDriver", "installed", "DEFAULT_LOGIN_TIMEOUT_S"]
