# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for browser-use: snapshot rendering, session crypto, and the gate.

No browser is launched here — the live-Chromium suite is
``tests/integration/test_browser_use_live.py``. What is tested here is the
logic a broken browser would otherwise hide: ref validation, what the model is
shown, what the session store writes, and which calls need a human.
"""

from __future__ import annotations

import json
import socket
from unittest.mock import patch

import pytest

from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin
from gaia.browser import session as session_store
from gaia.browser.errors import SessionStoreError
from gaia.browser.snapshot import REF_ATTR, ref_selector, render

# --------------------------------------------------------------------- refs


@pytest.mark.parametrize("ref", ["g1e1", "g4e12", "g99e999"])
def test_ref_selector_accepts_generated_refs(ref):
    assert ref_selector(ref) == f'[{REF_ATTR}="{ref}"]'


def test_a_ref_carries_the_snapshot_that_issued_it():
    """Staleness must fail, not silently hit a different element.

    Every page numbers its elements from e1. A live run clicked "e2" three
    times expecting one control and bounced between two pages, because e2 was
    "Back to shop" on one and "Widget B" on the other.
    """
    import re as _re

    assert _re.match(r"^g\d+e\d+$", "g4e12")
    with pytest.raises(ValueError):
        ref_selector("e12")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "nope",
        "e",
        "1",
        "e1x",
        # Unscoped refs are rejected now: every page numbers from e1, so an
        # unscoped ref from the previous page would resolve to a different
        # element instead of failing.
        "e1",
        "g1",
        "ge1",
        # The reason this is a whitelist and not an escape: a ref is
        # interpolated into a CSS selector, so model-supplied text must never
        # reach it.
        'g1e1"] , [href^="http',
        "g1e1']",
    ],
)
def test_ref_selector_rejects_anything_else(bad):
    with pytest.raises(ValueError, match="Invalid element ref"):
        ref_selector(bad)


# ----------------------------------------------------------------- rendering


def _snap(**over):
    base = {
        "url": "https://example.com/",
        "title": "Example",
        "elements": [
            {"ref": "g1e1", "role": "link", "name": "Learn more"},
            {"ref": "e2", "role": "textbox", "name": "Search", "value": "amd"},
            {
                "ref": "e3",
                "role": "select",
                "name": "Size",
                "value": "Large",
                "options": ["Small", "Large"],
            },
            {"ref": "e4", "role": "button", "name": "Send", "disabled": True},
        ],
        "truncated": False,
        "text": "Body text here.",
    }
    base.update(over)
    return base


def test_render_lists_every_element_with_its_ref():
    out = render(_snap())
    assert "Page: Example" in out
    assert "URL: https://example.com/" in out
    assert 'e1 link "Learn more"' in out
    assert 'e2 textbox "Search" (value: amd)' in out
    assert "(options: Small, Large)" in out
    assert "(disabled)" in out
    assert "Body text here." in out


def test_render_can_omit_page_text():
    out = render(_snap(), include_text=False)
    assert "Body text here." not in out
    assert 'e1 link "Learn more"' in out


def test_render_says_when_the_element_list_was_cut_short():
    """Silence here would read to the model as "that is the whole page"."""
    out = render(_snap(truncated=True))
    assert "truncated" in out.lower()


def test_render_handles_a_page_with_nothing_to_click():
    out = render(_snap(elements=[]))
    assert "none found" in out


# ------------------------------------------------------------ session store


@pytest.fixture
def keyring_and_home(tmp_path, monkeypatch):
    """An in-memory keyring and a throwaway ~/.gaia."""
    import keyring

    vault: dict = {}
    monkeypatch.setattr(
        keyring, "get_password", lambda s, u: vault.get((s, u)), raising=True
    )
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda s, u, v: vault.__setitem__((s, u), v),
        raising=True,
    )
    # Patch the module's own directory resolver, NOT pathlib.Path.home —
    # that would swap a global for the whole session and leak into any test
    # that reads a home-relative path.
    target = tmp_path / ".gaia" / "browser" / "sessions"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_store, "sessions_dir", lambda: target)
    return vault


def test_session_round_trips(keyring_and_home):
    state = {"cookies": [{"name": "sid", "value": "secret", "domain": ".example.com"}]}
    origin = session_store.save("https://example.com/login", state)
    assert origin == "https://example.com"
    assert session_store.load("https://example.com/anything") == state


def test_session_is_absent_until_saved(keyring_and_home):
    assert session_store.load("https://nothing-here.example") is None


def test_saved_session_is_not_readable_on_disk(keyring_and_home, tmp_path):
    """The whole point of the design: ciphertext on disk, key in the keyring."""
    session_store.save(
        "https://example.com", {"cookies": [{"name": "sid", "value": "s3cr3t"}]}
    )
    blobs = list((tmp_path / ".gaia" / "browser" / "sessions").glob("*.enc"))
    assert blobs, "no encrypted session was written"
    raw = blobs[0].read_bytes()
    assert b"s3cr3t" not in raw
    assert b"cookies" not in raw


def test_metadata_is_readable_and_carries_no_secret(keyring_and_home, tmp_path):
    session_store.save(
        "https://example.com", {"cookies": [{"name": "sid", "value": "s3cr3t"}]}
    )
    meta_files = list((tmp_path / ".gaia" / "browser" / "sessions").glob("*.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text())
    assert meta["origin"] == "https://example.com"
    assert meta["cookies"] == 1
    assert "s3cr3t" not in meta_files[0].read_text()


def test_a_session_cannot_be_replayed_against_another_origin(
    keyring_and_home, tmp_path
):
    """Origin is the AEAD's associated data, so a moved blob fails to open."""
    session_store.save("https://bank.example", {"cookies": [{"name": "sid"}]})
    sessions = tmp_path / ".gaia" / "browser" / "sessions"
    blob = next(sessions.glob("*.enc"))

    # Same ciphertext, filed under a different origin's slug.
    other = sessions / f"{session_store._slug('https://evil.example')}.enc"
    other.write_bytes(blob.read_bytes())

    with pytest.raises(SessionStoreError, match="Could not decrypt"):
        session_store.load("https://evil.example")


def test_forget_removes_both_files(keyring_and_home, tmp_path):
    session_store.save("https://example.com", {"cookies": []})
    assert session_store.forget("https://example.com") is True
    assert not list((tmp_path / ".gaia" / "browser" / "sessions").glob("*"))
    assert session_store.forget("https://example.com") is False


def test_listing_reports_saved_origins(keyring_and_home):
    session_store.save("https://a.example", {"cookies": []})
    session_store.save("https://b.example", {"cookies": []})
    assert {r["origin"] for r in session_store.listing()} == {
        "https://a.example",
        "https://b.example",
    }


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://Example.COM/path?q=1", "https://example.com"),
        ("http://example.com:8080/x", "http://example.com"),
        ("example.com", "https://example.com"),
    ],
)
def test_origin_normalisation(url, expected):
    assert session_store.origin_of(url) == expected


def test_origin_of_rejects_junk():
    with pytest.raises(SessionStoreError, match="Cannot determine the site"):
        session_store.origin_of("not a url")


# -------------------------------------------------------------- the gate


class _FakeDriver:
    """Stands in for a live browser context with a given cookie set."""

    started = True

    def __init__(self, hosts=(), raises=False):
        self._hosts = set(hosts)
        self._raises = raises

    def origin_has_cookies(self, origin):
        if self._raises:
            raise RuntimeError("browser is gone")
        from urllib.parse import urlparse

        host = (urlparse(origin).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self._hosts)


class _Agent(BrowserUseToolsMixin):
    """Bare mixin host — the gate must not depend on a full agent."""

    def _signed_in(self, origin, hosts=(), raises=False):
        """Put the agent on ``origin`` with a browser holding ``hosts`` cookies."""
        self._browser_current_origin = origin
        self._browser_driver = _FakeDriver(hosts, raises=raises)
        return self


def test_reading_the_open_web_is_not_gated():
    a = _Agent()
    for name in ("browser_open", "browser_snapshot", "browser_sessions"):
        assert a.browser_call_needs_confirmation(name) is False


def test_acting_on_an_unauthenticated_page_is_not_gated():
    a = _Agent()._signed_in("https://example.com", hosts=())
    assert a.browser_call_needs_confirmation("browser_click") is False
    assert a.browser_call_needs_confirmation("browser_type") is False


def test_ordinary_browsing_is_not_gated_just_because_a_site_sets_cookies():
    """Cookie presence is not a session.

    Almost every site sets one. Treating that as "signed in" gated a public
    weather page in a live run — a gate that fires on normal browsing is a gate
    people learn to click through.
    """
    a = _Agent()._signed_in("https://weather.example", hosts={"weather.example"})
    assert a.browser_call_needs_confirmation("browser_open") is False
    assert a.browser_call_needs_confirmation("browser_click") is False


def test_acting_inside_a_signed_in_session_is_gated():
    """The case that matters: a page could be steering the model."""
    a = _Agent()._signed_in("https://bank.example", hosts={"bank.example"})
    a._authenticated_origins().add("https://bank.example")
    assert a.browser_call_needs_confirmation("browser_click") is True
    assert a.browser_call_needs_confirmation("browser_type") is True


def test_a_sibling_origin_of_the_login_is_gated_too():
    """The hole this replaced.

    Cookies belong to the browser context, so signing in at
    accounts.google.com authenticates mail.google.com. Matching against the
    login origin left "Delete forever" on the mail domain ungated — and most
    real sign-ins split identity provider from product.
    """
    a = _Agent()._signed_in("https://mail.google.com", hosts={"google.com"})
    a._authenticated_origins().add("https://accounts.google.com")
    assert a.browser_call_needs_confirmation("browser_click") is True


def test_a_signed_in_session_does_not_gate_an_unrelated_site():
    a = _Agent()._signed_in("https://news.example", hosts={"bank.example"})
    assert a.browser_call_needs_confirmation("browser_click") is False


def test_navigating_inside_a_signed_in_session_is_gated():
    """A GET acts too — unsubscribe, logout and delete links are navigations."""
    a = _Agent()._signed_in("https://bank.example", hosts={"bank.example"})
    a._authenticated_origins().add("https://bank.example")
    assert a.browser_call_needs_confirmation("browser_open") is True


def test_navigating_with_no_browser_open_is_not_gated():
    """The ordinary first navigation of a run must not prompt."""
    assert _Agent().browser_call_needs_confirmation("browser_open") is False


def test_the_gate_fails_closed_when_the_browser_cannot_be_asked():
    """A gate that opens when it is confused is not a gate."""
    a = _Agent()._signed_in("https://bank.example", raises=True)
    a._authenticated_origins().add("https://other.example")
    assert a.browser_call_needs_confirmation("browser_click") is True


def test_the_mixin_declares_its_own_confirmation_hook():
    """Any agent composing the mixin must be gated, not just ChatAgent.

    `gaia agent init --tools browser_use` scaffolds `class Foo(Agent,
    BrowserUseToolsMixin)`; when the gate lived on ChatAgent, that agent got a
    live authenticated browser with no prompt at all.
    """
    assert "browser_call_needs_confirmation" in BrowserUseToolsMixin.CONFIRMATION_HOOKS


def test_signing_in_is_always_gated():
    assert _Agent().browser_call_needs_confirmation("browser_login") is True


def test_cleanup_is_safe_when_no_browser_was_opened():
    _Agent().cleanup_browser_use()


# ------------------------------------------------- registration without deps


def test_installed_reports_absent_rather_than_raising():
    """``find_spec`` RAISES on a missing parent package instead of returning None.

    The registration path calls this on every agent build, so letting that
    escape takes down agent construction on any install without Playwright —
    which is every core install. Exercises the real function; stubbing
    ``installed`` here would only prove the caller was called.
    """
    import importlib

    from gaia.browser import driver as browser_driver

    real_find_spec = importlib.util.find_spec

    def absent(name, *args, **kwargs):
        if name.split(".")[0] == "playwright":
            raise ModuleNotFoundError("No module named 'playwright'")
        return real_find_spec(name, *args, **kwargs)

    with patch.object(importlib.util, "find_spec", absent):
        assert browser_driver.installed() is False


def test_no_tools_are_registered_when_playwright_is_absent(monkeypatch):
    """A core install must not advertise a capability it cannot deliver."""
    from gaia.browser import driver as browser_driver

    monkeypatch.setattr(browser_driver, "installed", lambda: False)

    from gaia.agents.base import tools as tools_mod

    before = set(tools_mod._TOOL_REGISTRY)
    _Agent().register_browser_use_tools()
    assert set(tools_mod._TOOL_REGISTRY) - before == set()


# ------------------------------------------------------------------ SSRF


@pytest.mark.parametrize(
    "url",
    [
        # The one that matters: cloud metadata is a credential endpoint, and a
        # page can talk a model into navigating anywhere.
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "javascript:alert(1)",
    ],
)
def test_the_browser_refuses_addresses_off_the_public_internet(url, monkeypatch):
    monkeypatch.delenv("GAIA_BROWSER_ALLOW_PRIVATE", raising=False)
    assert BrowserUseToolsMixin._check_navigable(url) is not None


def test_public_urls_pass(monkeypatch):
    monkeypatch.delenv("GAIA_BROWSER_ALLOW_PRIVATE", raising=False)
    looked_up = []

    def public_dns(host, *args, **kwargs):
        looked_up.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 0))]

    # A unit test must not depend on the runner's DNS.
    monkeypatch.setattr("gaia.web.client.socket.getaddrinfo", public_dns)
    assert BrowserUseToolsMixin._check_navigable("https://example.com") is None
    assert looked_up == ["example.com"]


def test_local_addresses_need_an_explicit_opt_in(monkeypatch):
    """Driving a local dev server is legitimate — but never by default."""
    monkeypatch.delenv("GAIA_BROWSER_ALLOW_PRIVATE", raising=False)
    assert BrowserUseToolsMixin._check_navigable("http://127.0.0.1:5173/") is not None
    monkeypatch.setenv("GAIA_BROWSER_ALLOW_PRIVATE", "1")
    assert BrowserUseToolsMixin._check_navigable("http://127.0.0.1:5173/") is None


def test_the_opt_in_still_rejects_a_non_http_scheme(monkeypatch):
    monkeypatch.setenv("GAIA_BROWSER_ALLOW_PRIVATE", "1")
    assert BrowserUseToolsMixin._check_navigable("file:///etc/passwd") is not None


# ------------------------------------------- irreversible-action gating


BENIGN_LABELS = [
    "Submit request",
    "Create",
    "File claim",
    "Next",
    "Continue",
    "Save draft",
    "Apply filters",
    "Order by price",
    "Sort order",
    "Reorder list",
    "Search",
    "Send feedback",
    "Log in",
    "Add to cart",
    "Accept all",
    "Deploy now",
    "Approve order",
    "Learn more",
    "Go now",
    "Reset",
]

IRREVERSIBLE_LABELS = [
    "Send transfer now",
    "Delete account permanently",
    "Place order",
    "Confirm payment",
    "Buy now",
    "Checkout",
    "Withdraw funds",
    "Deactivate account",
    "Publish post",
    "Complete purchase",
    "Cancel subscription",
    "Erase all data",
    "Send money",
]


@pytest.mark.parametrize("label", BENIGN_LABELS)
def test_ordinary_controls_do_not_prompt(label):
    """The failure mode that matters most for this gate is over-firing.

    "Submit", "Send", "Order" and "Confirm" are among the most common words on
    the web. Gating them would put a prompt in front of every search box and
    sort control, and a gate that fires constantly is one people click through
    without reading — which is worse than not having it.
    """
    assert BrowserUseToolsMixin._looks_irreversible(label) is False


@pytest.mark.parametrize("label", IRREVERSIBLE_LABELS)
def test_irreversible_controls_prompt(label):
    assert BrowserUseToolsMixin._looks_irreversible(label) is True


def test_an_irreversible_click_is_gated_with_no_session_at_all():
    """The live-run gap: a transfer is no more undoable when signed out.

    T6.4 clicked "Send transfer now" unprompted because the gate keyed only on
    session state and the fixture had no session.
    """
    a = _Agent()
    a._browser_last_elements = {"g1e1": "Send transfer now"}
    assert a.browser_call_needs_confirmation("browser_click", {"ref": "g1e1"}) is True


def test_a_harmless_click_on_the_same_page_is_not_gated():
    a = _Agent()
    a._browser_last_elements = {"g1e1": "Send transfer now", "g1e2": "Back to account"}
    assert a.browser_call_needs_confirmation("browser_click", {"ref": "g1e2"}) is False


def test_an_unknown_ref_falls_through_to_session_rules():
    """A ref with no remembered label cannot be judged on its text."""
    a = _Agent()
    a._browser_last_elements = {}
    assert a.browser_call_needs_confirmation("browser_click", {"ref": "g1e9"}) is False


# ------------------------------------------------- the site gate (browser pane)
#
# Driven end to end: a real Agent with the Agent UI's SSE console, its prompts
# answered the way the UI answers them, and a recorded loopback bridge, so the
# tests see what the user is asked and what main is allowed to read.

import queue as _queue  # noqa: E402
import threading as _threading  # noqa: E402
import time as _time  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from gaia.agents.base.agent import Agent  # noqa: E402
from gaia.agents.base.tool_grants import SITE_ARG, grant_scope  # noqa: E402
from gaia.browser import driver as _browser_driver  # noqa: E402
from gaia.browser._pane_bridge import DISCOVERY_ENV  # noqa: E402
from gaia.ui.sse_handler import SSEOutputHandler  # noqa: E402

from .browser_pane_fake import FakeBridge  # noqa: E402
from .browser_pane_fake import fixture as _bridge_fixture  # noqa: E402


class _PaneProbe(Agent, BrowserUseToolsMixin):
    def __init__(self, **kwargs):
        self.config = SimpleNamespace(ui_session_id="chat-1")
        super().__init__(**kwargs)

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        self.register_browser_use_tools()

    def _create_console(self):
        return self._console_override


@pytest.fixture
def pane(tmp_path, monkeypatch):
    """A chat attached to a recorded desktop pane, with no Playwright installed."""
    from gaia.agents.base import tools as tools_mod

    saved = dict(tools_mod._TOOL_REGISTRY)
    bridge = FakeBridge()
    monkeypatch.setenv(
        DISCOVERY_ENV, str(bridge.write_discovery(tmp_path / "pane.json"))
    )
    monkeypatch.setattr(_browser_driver, "installed", lambda: False)
    handler = SSEOutputHandler()
    with patch("gaia.agents.base.agent.AgentSDK"):
        _PaneProbe._console_override = handler
        agent = _PaneProbe(silent_mode=True, skip_lemonade=True)
    yield agent, handler, bridge
    bridge.close()
    tools_mod._TOOL_REGISTRY.clear()
    tools_mod._TOOL_REGISTRY.update(saved)


def _drive(agent, handler, tool_name, args, answer=(False, False)):
    """Run one call; answer its prompt with ``(approved, always)``; return (result, prompts)."""
    outcome = {}
    worker = _threading.Thread(
        target=lambda: outcome.update(result=agent._execute_tool(tool_name, args)),
        daemon=True,
    )
    worker.start()
    prompts = []
    deadline = _time.monotonic() + 20
    while _time.monotonic() < deadline:
        try:
            event = handler.event_queue.get(timeout=0.05)
        except _queue.Empty:
            if not worker.is_alive():
                break
            continue
        if event.get("type") == "permission_request":
            prompts.append(event)
            approved, always = answer
            handler.resolve_tool_confirmation(
                approved=approved, always=always, confirm_id=event["confirm_id"]
            )
    worker.join(timeout=5)
    assert not worker.is_alive()
    return outcome["result"], prompts


@pytest.mark.allow_network
def test_loopback_pages_need_no_approval(pane):
    agent, handler, bridge = pane
    result, prompts = _drive(
        agent, handler, "browser_open", {"url": "http://127.0.0.1:5000/form.html"}
    )
    assert prompts == []
    assert result.startswith("(in the browser pane)")
    assert bridge.last("evaluate")["body"]["allowedOrigins"] == []


@pytest.mark.allow_network
def test_another_site_asks_first_and_a_no_reads_nothing(pane):
    agent, handler, bridge = pane
    result, [prompt] = _drive(
        agent, handler, "browser_open", {"url": "https://example.com/"}
    )
    assert prompt["args"][SITE_ARG] == "https://example.com"
    assert prompt["always_scope"] == "pages on example.com"
    assert result["status"] == "denied"
    assert not [r for r in bridge.requests if r["body"].get("op") == "open"]
    assert "evaluate" not in bridge.ops()


@pytest.mark.allow_network
def test_allow_once_reads_that_site_for_that_call_only(pane):
    agent, handler, bridge = pane
    _drive(
        agent,
        handler,
        "browser_open",
        {"url": "https://example.com/"},
        answer=(True, False),
    )
    opened = [r["body"] for r in bridge.requests if r["body"].get("op") == "open"][-1]
    assert opened["allowedOrigins"] == ["https://example.com"]
    assert bridge.last("evaluate")["body"]["allowedOrigins"] == ["https://example.com"]

    bridge.replies["origin"] = _bridge_fixture("origin_remote.json")
    _, prompts = _drive(agent, handler, "browser_snapshot", {})
    assert [p["args"][SITE_ARG] for p in prompts] == ["https://example.com"]


@pytest.mark.allow_network
def test_always_covers_every_pane_tool_on_that_site(pane):
    agent, handler, bridge = pane
    _drive(
        agent,
        handler,
        "browser_open",
        {"url": "https://example.com/"},
        answer=(True, True),
    )
    bridge.replies["origin"] = _bridge_fixture("origin_remote.json")
    result, prompts = _drive(agent, handler, "browser_find", {"text": "Price"})
    assert prompts == []
    assert result.startswith("(in the browser pane)")
    assert bridge.last("evaluate")["body"]["allowedOrigins"] == ["https://example.com"]


@pytest.mark.allow_network
def test_a_page_the_user_moved_off_loopback_is_asked_about(pane):
    agent, handler, bridge = pane
    bridge.replies["origin"] = _bridge_fixture("origin_remote.json")
    _, prompts = _drive(agent, handler, "browser_snapshot", {})
    assert len(prompts) == 1 and prompts[0]["args"][SITE_ARG] == "https://example.com"


@pytest.mark.allow_network
def test_the_model_cannot_name_its_own_site(pane):
    agent, handler, bridge = pane
    result, prompts = _drive(
        agent, handler, "browser_snapshot", {SITE_ARG: "http://127.0.0.1:1"}
    )
    assert prompts == []
    assert "Unexpected argument" in result["error"]
    assert "evaluate" not in bridge.ops()


def test_headless_browsing_offers_no_site_grant():
    """Unchanged: a headless call carries no SITE_ARG, so "always" is never offered."""
    assert grant_scope("browser_open", {"url": "https://example.com/"}) is None
    assert grant_scope("browser_snapshot", {SITE_ARG: "https://example.com"}).key == (
        "browser_site:https://example.com"
    )


def test_the_internet_switch_still_covers_the_new_tools():
    from gaia.agents.base.offline import offline_refusal

    for name in ("browser_screenshot", "browser_logs"):
        assert offline_refusal(name, {})["error_type"] == "internet_off"
    assert offline_refusal("browser_open", {"url": "http://localhost:5173/"}) is None


@pytest.mark.allow_network
def test_the_agent_can_look_at_its_own_screenshot_without_asking(
    pane, tmp_path, monkeypatch
):
    from pathlib import Path

    from gaia.security import PathValidator

    agent, handler, _bridge = pane
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia"))
    agent.path_validator = PathValidator(allowed_paths=[str(tmp_path / "work")])
    result, prompts = _drive(agent, handler, "browser_screenshot", {})
    assert prompts == [] and result["status"] == "success"
    shot = result["image_path"]
    assert agent.path_validator.validate_read(shot, prompt_user=False)[0]
    neighbour = Path(shot).with_name("someone_else.png")
    neighbour.write_bytes(b"x")
    assert not agent.path_validator.validate_read(str(neighbour), prompt_user=False)[0]
