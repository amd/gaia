# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""PaneDriver and its bridge client, against a recorded loopback bridge.

The replies come from ``tests/fixtures/browser_pane/bridge/``, which the
Electron tests feed through the real bridge-server.cjs too, so the two halves
of the wire are pinned to the same shapes.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

from gaia.browser import _pane_bridge
from gaia.browser._pane_bridge import (
    DISCOVERY_ENV,
    ERROR_CODES,
    PaneBridge,
    discover,
    is_loopback_origin,
)
from gaia.browser.driver import PlaywrightDriver
from gaia.browser.errors import (
    BrowserError,
    PageOffScope,
    PaneCannotActYet,
    PaneNavigationBlocked,
    PaneProtocolMismatch,
    PaneUnreachable,
)
from gaia.browser.pane_driver import PaneDriver
from gaia.browser.snapshot import _FIND_JS, _SNAPSHOT_JS

from .browser_pane_fake import TOKEN, FakeBridge, fixture

# The fake bridge is a real loopback HTTP server, so the request on the wire is what gets checked.
pytestmark = pytest.mark.allow_network

CONTRACT = (
    Path(__file__).resolve().parents[2]
    / "src/gaia/apps/webui/services/browser-pane/contract.d.ts"
)


@pytest.fixture
def bridge():
    fake = FakeBridge()
    yield fake
    fake.close()


@pytest.fixture
def discovery_file(tmp_path, monkeypatch):
    path = tmp_path / "run" / "browser-pane.json"
    monkeypatch.setenv(DISCOVERY_ENV, str(path))
    return path


def _driver(bridge, discovery_file) -> PaneDriver:
    bridge.write_discovery(discovery_file)
    disc = discover()
    return PaneDriver(PaneBridge(disc), "chat-1")


# ---------------------------------------------------------------- discovery


def test_no_discovery_file_means_no_desktop_app(discovery_file):
    assert discover() is None


def test_a_file_left_by_a_crashed_app_is_ignored(bridge, discovery_file):
    bridge.write_discovery(discovery_file, pid=2**22 + 12345)
    assert discover() is None


def test_an_unreadable_discovery_file_fails_loudly(discovery_file):
    discovery_file.parent.mkdir(parents=True)
    discovery_file.write_text("{not json")
    with pytest.raises(PaneUnreachable, match="not a valid discovery file"):
        discover()


def test_the_env_var_beats_the_default_path(bridge, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    default = tmp_path / "home" / "run" / "browser-pane.json"
    monkeypatch.delenv(DISCOVERY_ENV, raising=False)
    assert _pane_bridge.discovery_path() == default
    chosen = tmp_path / "dev" / "browser-pane.json"
    monkeypatch.setenv(DISCOVERY_ENV, str(chosen))
    assert _pane_bridge.discovery_path() == chosen


# ----------------------------------------------------------------- the wire


def test_every_call_carries_the_token_and_names_its_op(bridge, discovery_file):
    driver = _driver(bridge, discovery_file)
    driver.snapshot()
    req = bridge.last("evaluate")
    assert req["path"] == "/v1/evaluate"
    assert req["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert req["headers"]["Content-Type"] == "application/json"
    assert "Origin" not in req["headers"]


@pytest.mark.parametrize(
    "fixture_key,run",
    [
        ("evaluate", lambda d: d.snapshot()),
        ("origin", lambda d: d.current_origin()),
        ("screenshot", lambda d: d.screenshot()),
        (
            "logs",
            lambda d: d.logs("console", errors_only=True, pattern=None, new_only=False),
        ),
        ("navigate_back", lambda d: d.back()),
    ],
)
def test_request_bodies_match_the_shared_fixture(
    bridge, discovery_file, fixture_key, run
):
    """bridge-server.cjs parses requests.json; PaneDriver must send exactly those keys."""
    driver = _driver(bridge, discovery_file)
    run(driver)
    expected = fixture("requests.json")[fixture_key]
    op = "navigate" if fixture_key == "navigate_back" else fixture_key
    sent = bridge.requests[[r["op"] for r in bridge.requests].index(op)]["body"]
    assert set(sent) == set(expected)


def test_snapshot_sends_the_one_snapshot_script_over_every_frame(
    bridge, discovery_file
):
    driver = _driver(bridge, discovery_file)
    driver.snapshot()
    body = bridge.last("evaluate")["body"]
    assert body["script"] == _SNAPSHOT_JS
    assert body["frames"] == "all"
    assert body["args"]["gen"] == 1
    assert body["allowedOrigins"] == []


def test_a_scope_reaches_every_op_inside_it_and_no_further(bridge, discovery_file):
    driver = _driver(bridge, discovery_file)
    with driver.scoped(["https://example.com"]):
        driver.find("Price")
    driver.find("Price")
    first, second = [r["body"] for r in bridge.requests if r["op"] == "evaluate"]
    assert first["allowedOrigins"] == ["https://example.com"]
    assert first["script"] == _FIND_JS and first["args"] == ["Price", 160]
    assert second["allowedOrigins"] == []


def test_hello_refuses_another_protocol_and_names_the_side_to_update(
    bridge, discovery_file
):
    bridge.replies["hello"] = {**fixture("hello.json"), "protocol": 2}
    bridge.write_discovery(discovery_file)
    with pytest.raises(PaneProtocolMismatch, match="Update the GAIA Python package"):
        PaneBridge(discover()).hello()
    bridge.replies["hello"] = {**fixture("hello.json"), "protocol": 0}
    with pytest.raises(PaneProtocolMismatch, match="Update GAIA desktop"):
        PaneBridge(discover()).hello()


def test_a_bridge_that_does_not_answer_is_unreachable(bridge, discovery_file):
    bridge.write_discovery(discovery_file)
    disc = discover()
    bridge.close()
    with pytest.raises(PaneUnreachable, match="Restart GAIA desktop"):
        PaneBridge(disc, timeout_s=2).hello()


def test_a_refused_token_is_unreachable_not_a_page_error(bridge, discovery_file):
    bridge.replies["evaluate"] = (401, fixture("error_unauthorized.json"))
    with pytest.raises(PaneUnreachable, match="unauthorized"):
        _driver(bridge, discovery_file).snapshot()


# ------------------------------------------------------------- snapshots


class _FakeFrame:
    def __init__(self, snap=None, error=None):
        self._snap, self._error = snap, error

    def evaluate(self, script, args):
        assert script == _SNAPSHOT_JS
        if self._error:
            raise RuntimeError(self._error)
        return json.loads(json.dumps(self._snap))


class _FakePage:
    """The fixture page as Playwright sees it: one readable iframe, one cross-origin."""

    def __init__(self, frames_fixture):
        main, *subs = frames_fixture
        self.main_frame = _FakeFrame(main["value"])
        self.frames = [self.main_frame] + [
            _FakeFrame(f.get("value"), None if f["ok"] else f["error"]) for f in subs
        ]

    def evaluate(self, script, args):
        return self.main_frame.evaluate(script, args)


def test_snapshot_matches_the_headless_driver_on_the_same_page(bridge, discovery_file):
    frames = fixture("evaluate_snapshot.json")["frames"]
    headless = PlaywrightDriver()
    headless._page = _FakePage(frames)
    expected = headless._snapshot()

    got = _driver(bridge, discovery_file).snapshot()

    assert got == expected
    assert [e["ref"] for e in got["elements"]] == ["g1e1", "g1e2", "g1f1e1"]
    assert "Remember me" in got["text"]


def test_refs_from_a_frame_remember_which_frame_issued_them(bridge, discovery_file):
    driver = _driver(bridge, discovery_file)
    driver.snapshot()
    driver.screenshot("g1f1e1")
    clip = bridge.last("screenshot")["body"]["clip"]
    assert clip == {"frameId": "4:7", "selector": '[data-gaia-ref="g1e1"]'}


def test_the_driver_withholds_a_page_that_moved_off_its_scope(bridge, discovery_file):
    """Main checks too; this covers a page that moved while it was being read."""
    reply = fixture("evaluate_snapshot.json")
    reply["frames"][0]["value"] = {
        **reply["frames"][0]["value"],
        "url": "https://evil.example/x",
    }
    bridge.replies["evaluate"] = reply
    with pytest.raises(PageOffScope, match="https://evil.example"):
        _driver(bridge, discovery_file).snapshot()


def test_main_withholding_a_page_reaches_the_model_naming_the_origin(
    bridge, discovery_file
):
    bridge.replies["evaluate"] = (409, fixture("error_off_scope.json"))
    with pytest.raises(PageOffScope, match="now on https://evil.example"):
        _driver(bridge, discovery_file).snapshot()


def test_every_op_names_the_chat_so_main_can_refuse_one_the_user_left(
    bridge, discovery_file
):
    driver = _driver(bridge, discovery_file)
    driver.snapshot()
    driver.current_origin()
    driver.logs("console", errors_only=False, pattern=None, new_only=False)
    for req in bridge.requests:
        if req["op"] != "hello":
            assert req["body"]["sessionId"] == "chat-1", req["op"]


def test_a_chat_the_user_left_gets_a_message_written_for_the_model(
    bridge, discovery_file
):
    message = "The user switched GAIA desktop to another chat."
    bridge.replies["evaluate"] = (
        409,
        {"error": {"code": "not_in_desktop", "message": message, "data": {}}},
    )
    with pytest.raises(BrowserError, match="switched GAIA desktop") as err:
        _driver(bridge, discovery_file).snapshot()
    assert not isinstance(err.value, PaneUnreachable)


def test_a_blocked_redirect_names_where_the_page_tried_to_go(bridge, discovery_file):
    bridge.replies["navigate"] = (409, fixture("error_blocked_navigation.json"))
    bridge.replies["tabs"] = fixture("tabs_new_tab.json")
    with pytest.raises(PaneNavigationBlocked, match="https://evil.example/landing"):
        _driver(bridge, discovery_file).goto("http://127.0.0.1:5000/redirect.html")


# ---------------------------------------------------------------- goto


def test_goto_never_navigates_away_from_the_users_page(bridge, discovery_file):
    _driver(bridge, discovery_file).goto("http://127.0.0.1:5000/form.html")
    assert bridge.ops()[-2:] == ["tabs", "evaluate"]
    opened = bridge.requests[-2]["body"]
    assert opened == {
        "sessionId": "chat-1",
        "op": "open",
        "url": "http://127.0.0.1:5000/form.html",
        "allowedOrigins": [],
    }


def test_goto_uses_a_new_tab_page_in_place(bridge, discovery_file):
    bridge.replies["tabs"] = fixture("tabs_new_tab.json")
    _driver(bridge, discovery_file).goto("http://127.0.0.1:5000/form.html")
    assert bridge.last("navigate")["body"]["tabId"] == "tab-1"
    assert "open" not in [
        r["body"].get("op") for r in bridge.requests if r["op"] == "tabs"
    ]


# ------------------------------------------------------------ read tools


def test_screenshot_decodes_the_png(bridge, discovery_file):
    shot = _driver(bridge, discovery_file).screenshot()
    assert shot.png.startswith(b"\x89PNG")
    assert (shot.width, shot.height) == (1, 1)
    assert base64.b64encode(shot.png).decode() == fixture("screenshot.json")["png"]


def test_new_only_logs_start_after_the_last_entry_seen(bridge, discovery_file):
    driver = _driver(bridge, discovery_file)
    driver.logs("console", errors_only=False, pattern="TypeError", new_only=True)
    driver.logs("console", errors_only=False, pattern=None, new_only=True)
    driver.logs("console", errors_only=False, pattern=None, new_only=False)
    first, second, third = [r["body"] for r in bridge.requests if r["op"] == "logs"]
    assert (first["afterSeq"], first["pattern"]) == (0, "TypeError")
    assert second["afterSeq"] == fixture("logs_console.json")["lastSeq"]
    assert third["afterSeq"] == 0


def test_acting_tools_say_they_are_not_in_the_pane_yet(bridge, discovery_file):
    driver = _driver(bridge, discovery_file)
    with pytest.raises(PaneCannotActYet, match="coming in a later step"):
        driver.click("g1e2")
    with pytest.raises(PaneCannotActYet, match="browser_type"):
        driver.type_text("g1e1", "a@b.c")
    assert "act" not in bridge.ops()


# --------------------------------------------------- drift with the TS side


def test_error_codes_match_the_contract_and_the_fixture():
    in_contract = set(
        re.findall(
            r'\| "([a-z_]+)"',
            CONTRACT.read_text()
            .split("export type BridgeErrorCode =", 1)[1]
            .split(";", 1)[0],
        )
    )
    assert ERROR_CODES == set(fixture("error_codes.json")["codes"]) == in_contract


@pytest.mark.parametrize("origin,expected", fixture("loopback.json")["cases"])
def test_loopback_matches_the_shared_list(origin, expected):
    assert is_loopback_origin(origin) is expected
