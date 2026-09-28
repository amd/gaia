# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``Agent.CONFIRMATION_HOOKS`` — mixin-contributed confirmation decisions.

A static name set cannot express "gate this call only in this state". The
browser mixin gates a click only when the page sits inside a signed-in session,
and that decision has to reach ``Agent._tool_requires_confirmation`` from the
mixin that owns it — ``Agent`` precedes the tool mixins in every agent's MRO,
so a mixin-side override of that method is unreachable.

Before this hook the wiring lived as an override on one subclass, so any other
agent composing the mixin got an ungated browser. These tests pin the wiring
itself, which is what no gate test covered.
"""

from __future__ import annotations

from gaia.agents.base.agent import Agent


class _GateMixin:
    CONFIRMATION_HOOKS = ("needs_confirm",)

    def needs_confirm(self, tool_name: str) -> bool:
        return tool_name == "risky"


class _SecondMixin:
    CONFIRMATION_HOOKS = ("also_needs_confirm",)

    def also_needs_confirm(self, tool_name: str) -> bool:
        return tool_name == "other_risky"


class _BrokenMixin:
    CONFIRMATION_HOOKS = ("explodes",)

    def explodes(self, tool_name: str) -> bool:
        raise RuntimeError("hook is broken")


def _host(*mixins):
    """An Agent-shaped object without running Agent.__init__."""

    class _Host(Agent, *mixins):
        def _register_tools(self):  # pragma: no cover - abstract stub
            pass

        def _get_system_prompt(self):  # pragma: no cover - abstract stub
            return ""

    host = _Host.__new__(_Host)
    # _tools_registry is a read-only property that falls back to the global
    # registry when no per-instance snapshot was taken.
    host._instance_tools = {}
    return host


def test_a_hook_can_require_confirmation():
    assert _host(_GateMixin)._tool_requires_confirmation("risky") is True


def test_a_hook_leaves_other_tools_alone():
    assert _host(_GateMixin)._tool_requires_confirmation("harmless") is False


def test_two_mixins_each_keep_their_hook():
    """One duck-typed name would let the second mixin shadow the first."""
    host = _host(_GateMixin, _SecondMixin)
    assert set(type(host).confirmation_hooks()) >= {
        "needs_confirm",
        "also_needs_confirm",
    }
    assert host._tool_requires_confirmation("risky") is True
    assert host._tool_requires_confirmation("other_risky") is True


def test_a_broken_hook_closes_the_gate_rather_than_opening_it():
    assert _host(_BrokenMixin)._tool_requires_confirmation("anything") is True


def test_an_agent_with_no_hooks_is_unaffected():
    assert _host()._tool_requires_confirmation("anything") is False


def test_the_browser_mixin_gates_on_any_agent_that_composes_it():
    """The regression: this used to depend on ChatAgent overriding the gate."""
    from gaia.agents.tools.browser_use_tools import BrowserUseToolsMixin

    host = _host(BrowserUseToolsMixin)
    assert host._tool_requires_confirmation("browser_login") is True
