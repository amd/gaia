# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which browser a chat's agent uses: the desktop pane, or headless Playwright.

One rule, in ``BrowserUseToolsMixin._pane``. A live desktop app whose bridge
refuses must surface as ``PaneUnreachable`` and never quietly become a hidden
headless browser: the user is looking at the pane.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gaia.agents.base import tools as tools_mod
from gaia.agents.base.tool_grants import grant_scope
from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin
from gaia.browser import driver as browser_driver
from gaia.browser._pane_bridge import DISCOVERY_ENV
from gaia.browser.errors import BrowserUnavailable, PaneUnreachable
from gaia.browser.pane_driver import PaneDriver

from .browser_pane_fake import FakeBridge, fixture

pytestmark = pytest.mark.allow_network


class _Agent(BrowserUseToolsMixin):
    def __init__(self, session_id="chat-1"):
        self.config = SimpleNamespace(ui_session_id=session_id)
        self._tool_overrides = {}


@pytest.fixture(autouse=True)
def _restore_tool_registry():
    """@tool writes into a process-global dict other tests read."""
    saved = dict(tools_mod._TOOL_REGISTRY)
    yield
    tools_mod._TOOL_REGISTRY.clear()
    tools_mod._TOOL_REGISTRY.update(saved)


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


@pytest.fixture
def no_playwright(monkeypatch):
    monkeypatch.setattr(browser_driver, "installed", lambda: False)


@pytest.fixture
def playwright_launch(monkeypatch):
    """Playwright 'installed', with start() recorded instead of launching Chromium."""
    started = []
    monkeypatch.setattr(browser_driver, "installed", lambda: True)
    monkeypatch.setattr(
        browser_driver.PlaywrightDriver, "start", lambda self: started.append(self)
    )
    return started


# ------------------------------------------------------------ the route table


def test_attached_chat_uses_the_pane(bridge, discovery_file, no_playwright):
    bridge.write_discovery(discovery_file)
    agent = _Agent()
    assert isinstance(agent._ensure_driver(), PaneDriver)
    assert bridge.ops() == ["hello", "attach"]
    assert bridge.last("attach")["body"] == {"sessionId": "chat-1"}


def test_a_chat_with_no_ui_session_never_asks_the_pane(
    bridge, discovery_file, playwright_launch
):
    bridge.write_discovery(discovery_file)
    agent = _Agent(session_id=None)
    driver = agent._ensure_driver()
    assert driver.where == "(headless browser)"
    assert bridge.requests == []


def test_no_desktop_app_means_headless(discovery_file, playwright_launch):
    assert _Agent()._ensure_driver().where == "(headless browser)"
    assert len(playwright_launch) == 1


def test_a_crashed_apps_leftover_file_means_headless(
    bridge, discovery_file, playwright_launch
):
    bridge.write_discovery(discovery_file, pid=2**22 + 12345)
    assert _Agent()._ensure_driver().where == "(headless browser)"
    assert bridge.requests == []


def test_a_chat_not_open_in_the_window_means_headless(
    bridge, discovery_file, playwright_launch
):
    bridge.replies["attach"] = fixture("attach_not_in_desktop.json")
    bridge.write_discovery(discovery_file)
    assert _Agent()._ensure_driver().where == "(headless browser)"


def test_a_desktop_app_that_does_not_answer_never_falls_back(
    bridge, discovery_file, playwright_launch
):
    bridge.write_discovery(discovery_file)
    bridge.close()
    agent = _Agent()
    with pytest.raises(PaneUnreachable):
        agent._ensure_driver()
    assert playwright_launch == []
    assert agent._browser_driver is None
    # Asked again next call, so a restarted app is picked up.
    assert agent._browser_routed is False


def test_a_bridge_that_refuses_the_token_never_falls_back(
    bridge, discovery_file, playwright_launch
):
    bridge.replies["hello"] = (401, fixture("error_unauthorized.json"))
    bridge.write_discovery(discovery_file)
    with pytest.raises(PaneUnreachable, match="unauthorized"):
        _Agent()._ensure_driver()
    assert playwright_launch == []


def test_neither_browser_names_both_remedies(discovery_file, no_playwright):
    with pytest.raises(BrowserUnavailable) as err:
        _Agent()._ensure_driver()
    assert "GAIA desktop" in str(err.value)
    assert "amd-gaia[browser]" in str(err.value)


def test_the_route_is_decided_once_per_agent(bridge, discovery_file, no_playwright):
    bridge.write_discovery(discovery_file)
    agent = _Agent()
    agent._ensure_driver()
    agent._ensure_driver()
    assert bridge.ops() == ["hello", "attach"]


# --------------------------------------------------------------- registration


def _tool(agent, name):
    return agent._tool_overrides[name]["function"]


def test_the_pane_alone_registers_the_tools_on_this_agent_only(
    bridge, discovery_file, no_playwright
):
    bridge.write_discovery(discovery_file)
    before = set(tools_mod._TOOL_REGISTRY)
    agent = _Agent()
    agent.register_browser_use_tools()
    assert {
        "browser_open",
        "browser_snapshot",
        "browser_screenshot",
        "browser_logs",
        "browser_press",
        "browser_viewport",
        "browser_tabs",
        "browser_batch",
    } <= set(agent._tool_overrides)
    # Another chat's agent, built in the same process, must not inherit them.
    assert set(tools_mod._TOOL_REGISTRY) == before


def test_pane_only_tools_need_the_chat_attached(
    bridge, discovery_file, playwright_launch
):
    bridge.replies["attach"] = fixture("attach_not_in_desktop.json")
    bridge.write_discovery(discovery_file)
    agent = _Agent()
    agent.register_browser_use_tools()
    for name in (
        "browser_screenshot",
        "browser_logs",
        "browser_press",
        "browser_batch",
    ):
        assert name not in agent._tool_overrides
    assert "browser_open" in tools_mod._TOOL_REGISTRY


def test_nothing_registers_with_neither_browser(discovery_file, no_playwright):
    agent = _Agent()
    before = set(tools_mod._TOOL_REGISTRY)
    agent.register_browser_use_tools()
    assert agent._tool_overrides == {}
    assert set(tools_mod._TOOL_REGISTRY) == before


# ------------------------------------------------------------- tool results


@pytest.fixture
def pane_agent(bridge, discovery_file, no_playwright):
    bridge.write_discovery(discovery_file)
    agent = _Agent()
    agent.register_browser_use_tools()
    return agent


def test_every_result_starts_by_naming_the_pane(pane_agent):
    result = _tool(pane_agent, "browser_snapshot")()
    assert result.splitlines()[0] == "(in the browser pane)"
    assert "Page: Pane fixture form" in result


def test_a_pane_failure_also_names_the_pane(pane_agent, bridge):
    bridge.replies["evaluate"] = (409, fixture("error_off_scope.json"))
    result = _tool(pane_agent, "browser_snapshot")()
    assert result.splitlines()[0] == "(in the browser pane)"
    assert "https://evil.example" in result


def test_a_click_in_the_pane_lands_in_the_pane(pane_agent, bridge):
    _tool(pane_agent, "browser_snapshot")()
    result = _tool(pane_agent, "browser_click")(ref="g1e2")
    assert result.splitlines()[:2] == ["(in the browser pane)", "Clicked g1e2."]
    assert bridge.last("act")["body"]["action"] == {"kind": "click"}


def test_a_loopback_click_asks_only_when_its_label_is_irreversible(pane_agent, bridge):
    _tool(pane_agent, "browser_snapshot")()
    assert (
        pane_agent.browser_call_needs_confirmation("browser_click", {"ref": "g1e1"})
        is False
    )
    pane_agent._browser_last_elements["g1e2"] = "Create account"
    args = {"ref": "g1e2"}
    shown = pane_agent.confirmation_args_for_call("browser_click", args)
    assert pane_agent.browser_call_needs_confirmation("browser_click", args) is True
    assert "Create account" in shown["_risk_note"]
    # A risk note makes the prompt one-time: no "always" grant can cover it.
    assert grant_scope("browser_click", shown) is None


def _page_says(bridge, label):
    def reply(body):
        if "data-gaia-pick" not in json.dumps(body.get("args")):
            return fixture("evaluate_snapshot.json")
        return {
            "tabId": "tab-1",
            "url": "http://127.0.0.1:5000/picker.html",
            "frames": [
                {
                    "frameId": None,
                    "url": "http://127.0.0.1:5000/picker.html",
                    "ok": True,
                    "value": label,
                }
            ],
        }

    bridge.replies["evaluate"] = reply


def test_a_click_on_a_picked_element_reads_its_label_for_the_gate(pane_agent, bridge):
    _page_says(bridge, "Save")
    assert (
        pane_agent.browser_call_needs_confirmation("browser_click", {"ref": "p1"})
        is False
    )
    assert bridge.last("evaluate")["body"]["args"] == '[data-gaia-pick="p1"]'
    _page_says(bridge, "Delete everything")
    args = {"ref": "p1"}
    shown = pane_agent.confirmation_args_for_call("browser_click", args)
    assert pane_agent.browser_call_needs_confirmation("browser_click", args) is True
    assert "Delete everything" in shown["_risk_note"]


def test_a_picked_element_whose_label_cannot_be_read_asks_first(pane_agent, bridge):
    bridge.replies["evaluate"] = (409, fixture("error_off_scope.json"))
    args = {"ref": "p1"}
    assert pane_agent.browser_call_needs_confirmation("browser_click", args) is True
    assert (
        "could not be read"
        in pane_agent.confirmation_args_for_call("browser_click", args)["_risk_note"]
    )


def test_signing_in_is_left_to_the_user_in_the_pane(pane_agent, bridge):
    assert (
        pane_agent.browser_call_needs_confirmation(
            "browser_login", {"url": "https://x.example"}
        )
        is False
    )
    result = _tool(pane_agent, "browser_login")(url="https://x.example/login")
    assert "sign in" in result.lower() and "themselves" in result
    assert bridge.ops() == ["hello", "attach"]


def test_screenshot_saves_a_png_the_ui_can_show(pane_agent, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    result = _tool(pane_agent, "browser_screenshot")()
    assert result["browser"] == "(in the browser pane)"
    assert result["image_path"].startswith(str(tmp_path / "home"))
    with open(result["image_path"], "rb") as f:
        assert f.read().startswith(b"\x89PNG")
    assert "analyze_image" in result["next_step"]


def test_logs_render_one_line_per_entry(pane_agent):
    result = _tool(pane_agent, "browser_logs")(kind="console", errors_only=True)
    lines = result.splitlines()
    assert lines[0] == "(in the browser pane)"
    assert (
        "1 console messages on http://127.0.0.1:5000/broken.html (errors only):"
        == lines[1]
    )
    assert "Uncaught TypeError" in lines[2] and "broken.html:12" in lines[2]
