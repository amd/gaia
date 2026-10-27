# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Page boxes, cross-site frames and browser_eval, from the agent's side.

Runs against the recorded loopback bridge, so each test checks the request
that reached the pane and what the model reads back, not that a method ran.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from gaia.agents.base import console as console_mod
from gaia.agents.base import tools as tools_mod
from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tool_grants import SITE_ARG, grant_scope
from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin
from gaia.browser import driver as browser_driver
from gaia.browser._pane_bridge import DISCOVERY_ENV
from gaia.browser.errors import PageDialogOpen

from .browser_pane_fake import FakeBridge, fixture

pytestmark = pytest.mark.allow_network

PAY = "https://pay.example"


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
    tool(a, "browser_snapshot")()
    return a


def tool(agent, name):
    return agent._tool_overrides[name]["function"]


def box(**over):
    return {**fixture("dialog_get.json")["dialog"], **over}


def page_shows(bridge, dialog):
    """The pane's tab waits on ``dialog``: get reads it, answer answers it."""

    def reply(body):
        if body["op"] == "get":
            return {"tabId": "tab-1", "dialog": dialog}
        if body["op"] == "release":
            return {"dismissed": 1}
        return {
            **fixture("dialog_answered.json"),
            "answered": dialog,
        }

    bridge.replies["dialog"] = reply


# ------------------------------------------------------------- the wire


@pytest.mark.parametrize(
    "key,run",
    [
        ("dialog_get", lambda d: d.dialog()),
        ("dialog_answer", lambda d: d.answer_dialog(True, "Ada")),
        ("script", lambda d: d.run_script("localStorage.getItem('cartId')")),
    ],
)
def test_new_request_bodies_match_the_shared_fixture(agent, bridge, key, run):
    """bridge-server.cjs parses requests.json; PaneDriver must send those keys."""
    bridge.replies["dialog"] = fixture("dialog_answered.json")
    bridge.replies["script"] = fixture("script_ok.json")
    run(agent._ensure_driver())
    op = key.split("_")[0]
    assert set(bridge.last(op)["body"]) == set(fixture("requests.json")[key])


# ------------------------------------------------------- a click opens a box


def test_a_click_that_opens_a_box_says_so_and_does_not_read_the_frozen_page(
    agent, bridge
):
    bridge.replies["act"] = fixture("act_dialog.json")
    reads = bridge.ops().count("evaluate")
    out = tool(agent, "browser_click")(ref="g1e2")
    assert out.splitlines()[0] == "(in the browser pane)"
    assert 'Clicked g1e2. The page asks "Delete account? This cannot be undone."' in out
    assert (
        "browser_dialog(accept=True)" in out and "browser_dialog(accept=False)" in out
    )
    assert bridge.ops().count("evaluate") == reads


def test_any_step_on_a_page_waiting_on_a_box_says_nothing_was_done(agent, bridge):
    bridge.replies["evaluate"] = (409, fixture("error_dialog_open.json"))
    out = tool(agent, "browser_snapshot")()
    assert "Nothing was done: a box is open on the page." in out
    assert '"Delete account? This cannot be undone."' in out


@pytest.mark.parametrize(
    "dialog,expected",
    [
        (
            box(type="alert", message="Saved"),
            'notice "Saved". Close it with browser_dialog(accept=True)',
        ),
        (
            box(type="beforeunload", message="Leave?"),
            "Leave with browser_dialog(accept=True)",
        ),
    ],
)
def test_each_kind_of_box_is_described_with_how_to_answer_it(dialog, expected):
    assert expected in str(PageDialogOpen(dialog, done=True))


def test_the_batch_stops_at_the_step_that_opened_a_box(agent, bridge):
    bridge.replies["act"] = fixture("act_dialog.json")
    before = len(bridge.requests)
    out = tool(agent, "browser_batch")(
        steps=[
            {"tool": "browser_click", "ref": "g1e2"},
            {"tool": "browser_type", "ref": "g1e1", "text": "ada@example.com"},
        ]
    )
    sent = [r["op"] for r in bridge.requests[before:]]
    assert sent.count("act") == 1 and "evaluate" not in sent
    assert "Ran 1 of 2 steps:" in out
    assert (
        "Step 1 (browser_click) opened a box on the page, so the batch stopped." in out
    )
    assert "browser_dialog(accept=True)" in out


# ---------------------------------------------------------- browser_dialog


def test_accepting_a_confirm_sends_ok_and_reads_the_page(agent, bridge):
    page_shows(bridge, box(type="confirm", message="Save this draft?"))
    out = tool(agent, "browser_dialog")(accept=True)
    sent = bridge.last("dialog")["body"]
    assert sent["op"] == "answer" and sent["accept"] is True
    assert 'Pressed OK on "Save this draft?".' in out
    assert bridge.ops()[-1] == "evaluate"
    assert "Page: Pane fixture form" in out


def test_a_second_box_right_after_the_first_is_reported_instead_of_a_read(
    agent, bridge
):
    answered = {
        **fixture("dialog_answered.json"),
        "dialog": box(type="alert", message="Done"),
    }
    bridge.replies["dialog"] = answered
    out = tool(agent, "browser_dialog")(accept=True)
    assert 'notice "Done"' in out
    assert bridge.ops()[-1] == "dialog"


def test_a_plain_confirm_on_loopback_is_answered_without_asking(agent, bridge):
    page_shows(bridge, box(message="Save this draft?"))
    args = {"accept": True}
    shown = agent.confirmation_args_for_call("browser_dialog", args)
    assert agent.browser_call_needs_confirmation("browser_dialog", args) is False
    assert SITE_ARG not in shown


def test_ok_on_a_box_naming_an_irreversible_action_always_asks_once(agent, bridge):
    page_shows(bridge, box())
    args = {"accept": True}
    shown = agent.confirmation_args_for_call("browser_dialog", args)
    assert agent.browser_call_needs_confirmation("browser_dialog", args) is True
    assert "Delete account? This cannot be undone." in shown["_risk_note"]
    assert grant_scope("browser_dialog", shown) is None


def test_cancel_and_closing_a_notice_never_ask(agent, bridge):
    page_shows(bridge, box())
    assert (
        agent.browser_call_needs_confirmation("browser_dialog", {"accept": False})
        is False
    )
    page_shows(bridge, box(type="alert", message="Delete account done", url=f"{PAY}/x"))
    assert (
        agent.browser_call_needs_confirmation("browser_dialog", {"accept": True})
        is False
    )


def test_ok_on_another_site_asks_for_that_site_and_carries_its_approval(agent, bridge):
    page_shows(bridge, box(message="Empty the cart?", url=f"{PAY}/cart"))
    args = {"accept": True}
    shown = agent.confirmation_args_for_call("browser_dialog", args)
    assert agent.browser_call_needs_confirmation("browser_dialog", args) is True
    assert shown[SITE_ARG] == PAY
    tool(agent, "browser_dialog")(**args)
    answer = [
        r["body"]
        for r in bridge.requests
        if r["op"] == "dialog" and r["body"]["op"] == "answer"
    ][-1]
    assert answer["allowedOrigins"] == [PAY]


def test_a_box_the_gate_cannot_read_asks_first(agent, bridge):
    bridge.replies["dialog"] = (409, fixture("error_off_scope.json"))
    args = {"accept": True}
    shown = agent.confirmation_args_for_call("browser_dialog", args)
    assert agent.browser_call_needs_confirmation("browser_dialog", args) is True
    assert "could not be read" in shown["_risk_note"]


# ------------------------------------------------------- the end of a turn


def test_the_turn_end_dismisses_boxes_only_after_the_pane_was_used(bridge):
    a = _Agent()
    a.register_browser_use_tools()
    a._ensure_driver()
    a.browser_end_turn()
    assert "dialog" not in bridge.ops()
    tool(a, "browser_snapshot")()
    page_shows(bridge, None)
    a.browser_end_turn()
    assert bridge.last("dialog")["body"] == fixture("requests.json")["dialog_release"]
    a.browser_end_turn()
    assert bridge.ops().count("dialog") == 1


def test_every_agent_runs_the_browser_turn_end_hook():
    composed = type("Composed", (Agent, BrowserUseToolsMixin), {})
    assert "browser_end_turn" in composed.turn_end_hooks()


def test_a_failing_turn_end_hook_is_logged_and_the_rest_still_run(caplog):
    ran = []

    class Hooks:
        turn_end_hooks = staticmethod(lambda: ("boom", "after"))

        def boom(self):
            raise RuntimeError("bridge gone")

        def after(self):
            ran.append("after")

    Agent._run_turn_end_hooks(Hooks())
    assert ran == ["after"]
    assert "turn-end hook boom failed: bridge gone" in caplog.text


# ----------------------------------------------------------- frames


def snapshot_with_frame(origin, name="Say hello"):
    snap = copy.deepcopy(fixture("evaluate_snapshot.json"))
    frame = snap["frames"][1]
    frame["url"] = f"{origin}/widget.html"
    frame["value"]["url"] = frame["url"]
    frame["value"]["elements"] = [{"ref": "g1e1", "role": "button", "name": name}]
    return snap


@pytest.fixture
def framed(bridge):
    bridge.replies["evaluate"] = snapshot_with_frame(PAY)
    a = _Agent()
    a.register_browser_use_tools()
    tool(a, "browser_snapshot")()
    return a


def test_a_click_in_a_frame_from_another_site_asks_for_that_site(framed, bridge):
    args = {"ref": "g1f1e1"}
    shown = framed.confirmation_args_for_call("browser_click", args)
    assert framed.browser_call_needs_confirmation("browser_click", args) is True
    assert shown[SITE_ARG] == PAY
    tool(framed, "browser_click")(**args)
    sent = bridge.last("act")["body"]
    assert sent["target"] == {"frameId": "4:7", "selector": '[data-gaia-ref="g1e1"]'}
    assert sent["allowedOrigins"] == [PAY]


def test_a_click_in_the_page_itself_still_needs_no_approval_on_loopback(framed):
    assert (
        framed.browser_call_needs_confirmation("browser_click", {"ref": "g1e2"})
        is False
    )


def test_a_loopback_frame_needs_no_approval(bridge):
    bridge.replies["evaluate"] = snapshot_with_frame("http://localhost:5000")
    a = _Agent()
    a.register_browser_use_tools()
    tool(a, "browser_snapshot")()
    assert (
        a.browser_call_needs_confirmation("browser_click", {"ref": "g1f1e1"}) is False
    )


def test_an_irreversible_label_inside_a_frame_asks_every_time(bridge):
    bridge.replies["evaluate"] = snapshot_with_frame(
        "http://localhost:5000", name="Delete account"
    )
    a = _Agent()
    a.register_browser_use_tools()
    tool(a, "browser_snapshot")()
    args = {"ref": "g1f1e1"}
    shown = a.confirmation_args_for_call("browser_click", args)
    assert a.browser_call_needs_confirmation("browser_click", args) is True
    assert "Delete account" in shown["_risk_note"]


# ------------------------------------------------------------ browser_eval


def test_eval_runs_the_expression_and_returns_its_json(agent, bridge):
    bridge.replies["script"] = fixture("script_ok.json")
    out = tool(agent, "browser_eval")(expression="localStorage.getItem('cartId')")
    assert (
        bridge.last("script")["body"]["expression"] == "localStorage.getItem('cartId')"
    )
    assert out.splitlines() == [
        "(in the browser pane)",
        "Value on http://127.0.0.1:5000/app-state.html:",
        '"c-4821"',
    ]


def test_a_page_error_comes_back_as_text(agent, bridge):
    bridge.replies["script"] = fixture("script_error.json")
    out = tool(agent, "browser_eval")(expression="cart.length")
    assert "The script failed: ReferenceError: cart is not defined" in out


def test_eval_off_loopback_is_refused_and_never_asked_about(agent, bridge):
    bridge.replies["script"] = (
        409,
        {
            "error": {
                "code": "unsupported",
                "message": "browser_eval runs only on the user's own app on this computer (localhost), and this page is on https://shop.example.",
                "data": {},
            }
        },
    )
    assert (
        agent.browser_call_needs_confirmation("browser_eval", {"expression": "1"})
        is False
    )
    out = tool(agent, "browser_eval")(expression="document.cookie")
    assert "runs only on the user's own app" in out


def test_eval_is_not_a_batch_step(agent, bridge):
    out = tool(agent, "browser_batch")(
        steps=[{"tool": "browser_eval", "expression": "1"}]
    )
    assert out.startswith("Error: step 1 uses browser_eval")
    assert "script" not in bridge.ops()
