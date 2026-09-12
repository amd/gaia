# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Live-Chromium tests for the browser-use driver.

Everything here drives a real browser against a local HTML file — no network,
so the suite is deterministic and safe in CI. What it proves is exactly what a
mock cannot: that the injected snapshot script runs in a real page, that refs
resolve back to real elements, and that Playwright's thread-affinity does not
bite when each caller arrives on a different thread.

Skipped when the ``[browser]`` extra is not installed.
"""

from __future__ import annotations

import threading
import time

import pytest

from gaia.browser import driver as browser_driver
from gaia.browser.errors import ElementNotFound, LoginTimedOut
from gaia.browser.snapshot import render

pytestmark = pytest.mark.skipif(
    not browser_driver.installed(), reason="playwright not installed ([browser] extra)"
)

PAGE = """<!doctype html><title>Fixture</title><body>
<label for="nm">Full name</label><input id="nm" type="text">
<select id="sz" aria-label="Size"><option>Small</option><option>Large</option></select>
<input type="password" id="pw" aria-label="Password" value="hunter2">
<input type="checkbox" id="ck" checked aria-label="Subscribe">
<button id="bt" disabled>Nope</button>
<div style="display:none"><a href="#h1">DISPLAYNONE</a></div>
<div aria-hidden="true"><a href="#h2">ARIAHIDDEN</a></div>
<a id="go" href="#arrived">Go now</a>
</body>"""


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    p = tmp_path_factory.mktemp("browser") / "fixture.html"
    p.write_text(PAGE, encoding="utf-8")
    return p.as_uri()


@pytest.fixture(scope="module")
def driver():
    d = browser_driver.PlaywrightDriver(headless=True)
    d.start()
    yield d
    d.close()


def _by_role(snap, role):
    return [e for e in snap["elements"] if e["role"] == role]


def test_snapshot_finds_the_visible_controls(driver, page_url):
    snap = driver.goto(page_url)
    assert snap["title"] == "Fixture"
    roles = {e["role"] for e in snap["elements"]}
    assert {"textbox", "select", "password", "checkbox", "button", "link"} <= roles


def test_hidden_and_aria_hidden_elements_are_left_out(driver, page_url):
    """aria-hidden is inherited, so an element inside a hidden container is out.

    Distinct sentinel names on purpose: "aria-hidden link" contains "hidden
    link", so a substring assertion could pass while the bug was live.
    """
    snap = driver.goto(page_url)
    names = " ".join(e["name"] for e in snap["elements"])
    assert "DISPLAYNONE" not in names
    assert "ARIAHIDDEN" not in names


def test_label_and_aria_label_become_the_name(driver, page_url):
    snap = driver.goto(page_url)
    assert _by_role(snap, "textbox")[0]["name"] == "Full name"
    assert _by_role(snap, "select")[0]["name"] == "Size"


def test_a_password_value_never_reaches_the_model(driver, page_url):
    """The one field whose contents must not enter the context window."""
    snap = driver.goto(page_url)
    assert "hunter2" not in render(snap)
    assert all("hunter2" not in str(e.get("value", "")) for e in snap["elements"])


def test_disabled_and_checked_state_are_reported(driver, page_url):
    snap = driver.goto(page_url)
    assert _by_role(snap, "button")[0]["disabled"] is True
    assert _by_role(snap, "checkbox")[0]["checked"] is True


def test_clicking_a_ref_navigates(driver, page_url):
    snap = driver.goto(page_url)
    ref = next(e["ref"] for e in snap["elements"] if e["name"] == "Go now")
    after = driver.click(ref)
    assert after["url"].endswith("#arrived")


def test_typing_fills_the_field(driver, page_url):
    snap = driver.goto(page_url)
    ref = _by_role(snap, "textbox")[0]["ref"]
    after = driver.type_text(ref, "Ada Lovelace")
    assert _by_role(after, "textbox")[0]["value"] == "Ada Lovelace"


def test_typing_into_a_select_picks_that_option(driver, page_url):
    """``<select>`` is routed to select_option rather than fill."""
    snap = driver.goto(page_url)
    ref = _by_role(snap, "select")[0]["ref"]
    after = driver.type_text(ref, "Large")
    assert _by_role(after, "select")[0]["value"] == "Large"


def test_a_stale_ref_is_an_actionable_error(driver, page_url):
    driver.goto(page_url)
    with pytest.raises(ElementNotFound, match="browser_snapshot"):
        driver.click("e9999")


def test_refs_are_reissued_on_every_snapshot(driver, page_url):
    """Refs are snapshot-scoped; a previous page's stamps must not survive."""
    driver.goto(page_url)
    driver.goto("about:blank")
    with pytest.raises(ElementNotFound):
        driver.click("e1")


def test_calls_from_many_threads_all_work(driver, page_url):
    """The reason the driver is thread-confined at all.

    Every agent tool body runs on a fresh daemon thread, so a driver that only
    worked from its creating thread would fail on the second tool call.
    """
    driver.goto(page_url)
    results, errors = [], []

    def _worker():
        try:
            results.append(len(driver.snapshot()["elements"]))
        except BaseException as e:  # noqa: BLE001 — reported below
            errors.append(e)

    threads = [threading.Thread(target=_worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"threaded snapshot failed: {errors}"
    assert len(results) == 6
    assert len(set(results)) == 1, "same page returned different element counts"


def test_the_browser_survives_being_reused(driver, page_url):
    """A persistent browser is the whole performance argument."""
    for _ in range(3):
        assert driver.goto(page_url)["title"] == "Fixture"


def test_storage_state_is_retrievable(driver, page_url):
    """browser_login persists whatever this returns, so it must be callable.

    Regression guard: the reader was briefly named for the same attribute the
    constructor sets, so the instance attribute shadowed the method.
    """
    driver.goto(page_url)
    state = driver.storage_state()
    assert isinstance(state, dict)
    assert "cookies" in state


def test_current_url_reports_the_open_page(driver, page_url):
    driver.goto(page_url)
    assert driver.current_url().startswith("file://")


# ------------------------------------------------------- login inference

# Served over real HTTP, not file:// — browsers refuse cookies on a file
# origin, so a file-served fixture cannot reproduce the false positive at all
# and the test would pass against the very bug it is meant to catch.
EMAIL_FIRST = """<!doctype html><title>Sign in</title><body>
<h1>Sign in</h1>
<input id="email" type="email" placeholder="Email">
<button id="next">Next</button>
<script>
  // What every real email-first page does: no password field until step two,
  // and cookies that keep arriving after the page has settled (analytics
  // beacons, a lazily-issued session id). The delayed one is the important
  // half — a cookie already present when the wait begins is in the baseline
  // and can never look "new", so an immediate-only fixture cannot reproduce
  // the false positive.
  document.cookie = "csrf=abc123; path=/";
  setTimeout(() => { document.cookie = "analytics=xyz789; path=/"; }, 1500);
  document.getElementById('next').onclick = () => {
    document.body.innerHTML =
      '<input id="pw" type="password" placeholder="Password">' +
      '<button id="go">Sign in</button>';
  };
</script>
</body>"""


@pytest.fixture(scope="module")
def http_login_url(tmp_path_factory):
    """Serve the email-first fixture on loopback so cookies actually apply."""
    import functools
    import http.server
    import socketserver
    import threading as _threading

    root = tmp_path_factory.mktemp("loginsrv")
    (root / "signin.html").write_text(EMAIL_FIRST, encoding="utf-8")

    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(root)
    )
    srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
    _threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/signin.html"
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_cookie_really_is_set_on_the_fixture(driver, http_login_url):
    """Guards the guard.

    If the fixture stopped setting a cookie, the regression test below would
    pass against the old buggy heuristic too — which is exactly how the first
    version of it was worthless.
    """
    driver.goto(http_login_url)
    names = {c.get("name") for c in driver.cookies()}
    assert "csrf" in names


def test_step_one_of_an_email_first_flow_is_not_mistaken_for_success(
    driver, http_login_url
):
    """The regression this heuristic exists for.

    Google and Microsoft show no password field on step one and set cookies
    immediately. "No password visible + a new cookie" called that a completed
    sign-in about a second in, and saved a logged-out session.
    """
    with pytest.raises(LoginTimedOut):
        driver.wait_for_login(http_login_url, timeout_s=8, poll_s=0.5)


def test_nothing_is_concluded_inside_the_minimum_dwell(driver, page_url):
    """A page with no password field at all must still wait out the dwell."""
    t0 = time.monotonic()
    with pytest.raises(LoginTimedOut):
        driver.wait_for_login(page_url, timeout_s=2, poll_s=0.25)
    assert time.monotonic() - t0 >= 1.5
