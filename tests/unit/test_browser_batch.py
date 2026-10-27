# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""browser_batch: several pane steps in one call, never past a prompt.

Runs against the recorded loopback bridge, so each test checks which ops
reached the pane and what they carried, not that a method was called.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gaia.agents.base import console as console_mod
from gaia.agents.base import tools as tools_mod
from gaia.agents.base.console import SilentConsole
from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin
from gaia.browser import driver as browser_driver
from gaia.browser._pane_bridge import DISCOVERY_ENV

from .browser_pane_fake import FakeBridge, fixture

pytestmark = pytest.mark.allow_network


class _Agent(BrowserUseToolsMixin):
    def __init__(self):
        self.config = SimpleNamespace(ui_session_id="chat-1")
        self._tool_overrides = {}
        self.console = SilentConsole()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    saved = dict(tools_mod._TOOL_REGISTRY)
    monkeypatch.setattr(console_mod, "auto_approve_env_enabled", lambda: False)
    monkeypatch.setattr(browser_driver, "installed", lambda: False)
    yield
    tools_mod._TOOL_REGISTRY.clear()
    tools_mod._TOOL_REGISTRY.update(saved)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    fake = FakeBridge()
    path = tmp_path / "run" / "browser-pane.json"
    monkeypatch.setenv(DISCOVERY_ENV, str(path))
    fake.write_discovery(path)
    yield fake
    fake.close()


@pytest.fixture
def agent(bridge):
    a = _Agent()
    a.register_browser_use_tools()
    a._tool_overrides["browser_snapshot"]["function"]()
    return a


def batch(agent, steps):
    return agent._tool_overrides["browser_batch"]["function"](steps=steps)


def acts(bridge):
    return [r["body"] for r in bridge.requests if r["op"] == "act"]


def test_steps_share_the_refs_of_the_snapshot_before_the_batch(agent, bridge):
    before = len(bridge.requests)
    out = batch(
        agent,
        [
            {"tool": "browser_type", "ref": "g1e1", "text": "ada@example.com"},
            {"tool": "browser_click", "ref": "g1e2"},
        ],
    )
    assert [r["op"] for r in bridge.requests[before:]].count("act") == 2
    assert [a["action"]["kind"] for a in acts(bridge)] == ["fill", "click"]
    assert acts(bridge)[0]["action"]["text"] == "ada@example.com"
    # One read at the end, after both actions.
    assert bridge.ops()[-1] == "evaluate"
    assert out.splitlines()[:2] == ["(in the browser pane)", "Ran 2 of 2 steps:"]
    assert "Page: Pane fixture form" in out


def test_the_batch_stops_before_an_irreversible_click(agent, bridge):
    agent._browser_last_elements["g1e2"] = "Create account"
    out = batch(
        agent,
        [
            {"tool": "browser_type", "ref": "g1e1", "text": "ada@example.com"},
            {"tool": "browser_click", "ref": "g1e2"},
        ],
    )
    assert [a["action"]["kind"] for a in acts(bridge)] == ["fill"]
    assert "Stopped before step 2 (browser_click)" in out
    assert "browser_click(ref='g1e2') on its own" in out


def test_the_batch_stops_before_opening_a_site_nobody_approved(agent, bridge):
    before = len(bridge.requests)
    out = batch(
        agent,
        [
            {"tool": "browser_open", "url": "https://example.com/"},
            {"tool": "browser_click", "ref": "g1e2"},
            {"tool": "browser_screenshot"},
        ],
    )
    assert not {"tabs", "navigate", "act", "screenshot"} & {
        r["op"] for r in bridge.requests[before:]
    }
    assert "Ran 0 of 3 steps" in out
    assert "Stopped before step 1 (browser_open)" in out


def test_a_site_the_user_always_allows_runs_with_that_site_in_scope(agent, bridge):
    args = {"url": "https://example.com/"}
    agent.console.grant_call_for_session(
        "browser_open", agent.confirmation_args_for_call("browser_open", args)
    )
    out = batch(agent, [{"tool": "browser_open", **args}])
    assert "Ran 1 of 1 steps" in out
    opened = bridge.last("tabs")["body"]
    assert opened["op"] == "open"
    assert opened["allowedOrigins"] == ["https://example.com"]


def test_a_failed_step_ends_the_batch_with_its_reason(agent, bridge):
    bridge.replies["act"] = (409, fixture("error_user_active.json"))
    out = batch(
        agent,
        [
            {"tool": "browser_click", "ref": "g1e1"},
            {"tool": "browser_click", "ref": "g1e2"},
        ],
    )
    assert len(acts(bridge)) == 1
    assert "Step 1 (browser_click) failed" in out
    assert "Stop using the browser" in out


@pytest.mark.parametrize(
    "steps,message",
    [
        ([], "non-empty list"),
        ([{"tool": "browser_batch", "steps": []}], "a batch can use"),
        ([{"tool": "browser_open", "url": "https://x.example", "_site": "x"}], "_site"),
        ([{"tool": "browser_type", "ref": "g1e1"}], "needs text"),
        ([{"tool": "browser_click", "ref": "g1e1"}] * 11, "at most 10"),
    ],
)
def test_bad_steps_are_refused_before_anything_runs(agent, bridge, steps, message):
    before = len(bridge.requests)
    out = batch(agent, steps)
    assert out.startswith("Error:") and message in out
    assert len(bridge.requests) == before


def test_steps_given_as_json_text_or_with_nested_args_both_run(agent, bridge):
    out = batch(agent, '[{"tool": "browser_press", "args": {"key": "Escape"}}]')
    assert "Ran 1 of 1 steps" in out
    assert acts(bridge)[-1]["action"] == {"kind": "press", "key": "Escape"}
