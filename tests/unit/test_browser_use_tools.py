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

import pytest

from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin
from gaia.browser import session as session_store
from gaia.browser.errors import SessionStoreError
from gaia.browser.snapshot import REF_ATTR, ref_selector, render

# --------------------------------------------------------------------- refs


@pytest.mark.parametrize("ref", ["e1", "e12", "e999"])
def test_ref_selector_accepts_generated_refs(ref):
    assert ref_selector(ref) == f'[{REF_ATTR}="{ref}"]'


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "nope",
        "e",
        "1",
        "e1x",
        # The reason this is a whitelist and not an escape: a ref is
        # interpolated into a CSS selector, so model-supplied text must never
        # reach it.
        'e1"] , [href^="http',
        "e1']",
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
            {"ref": "e1", "role": "link", "name": "Learn more"},
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


def test_acting_inside_a_signed_in_session_is_gated():
    """The case that matters: a page could be steering the model."""
    a = _Agent()._signed_in("https://bank.example", hosts={"bank.example"})
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
    assert a.browser_call_needs_confirmation("browser_click") is True


def test_a_signed_in_session_does_not_gate_an_unrelated_site():
    a = _Agent()._signed_in("https://news.example", hosts={"bank.example"})
    assert a.browser_call_needs_confirmation("browser_click") is False


def test_navigating_inside_a_signed_in_session_is_gated():
    """A GET acts too — unsubscribe, logout and delete links are navigations."""
    a = _Agent()._signed_in("https://bank.example", hosts={"bank.example"})
    assert a.browser_call_needs_confirmation("browser_open") is True


def test_navigating_with_no_browser_open_is_not_gated():
    """The ordinary first navigation of a run must not prompt."""
    assert _Agent().browser_call_needs_confirmation("browser_open") is False


def test_the_gate_fails_closed_when_the_browser_cannot_be_asked():
    """A gate that opens when it is confused is not a gate."""
    a = _Agent()._signed_in("https://bank.example", raises=True)
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
    assert BrowserUseToolsMixin._check_navigable("https://example.com") is None


def test_local_addresses_need_an_explicit_opt_in(monkeypatch):
    """Driving a local dev server is legitimate — but never by default."""
    monkeypatch.delenv("GAIA_BROWSER_ALLOW_PRIVATE", raising=False)
    assert BrowserUseToolsMixin._check_navigable("http://127.0.0.1:5173/") is not None
    monkeypatch.setenv("GAIA_BROWSER_ALLOW_PRIVATE", "1")
    assert BrowserUseToolsMixin._check_navigable("http://127.0.0.1:5173/") is None


def test_the_opt_in_still_rejects_a_non_http_scheme(monkeypatch):
    monkeypatch.setenv("GAIA_BROWSER_ALLOW_PRIVATE", "1")
    assert BrowserUseToolsMixin._check_navigable("file:///etc/passwd") is not None
