# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The agent process never starts CPython's WMI query thread.

On Windows a timed-out ``_wmi.exec_query`` leaves a thread behind that later
crashes the process with an access violation, so the sidecar answers every
``platform`` question without it.
"""

import ast
import platform
import sys
from pathlib import Path

import pytest
from gaia_agent import entry, platform_guard

PACKAGING_SERVER = Path(__file__).resolve().parents[1] / "packaging" / "server.py"
# Taken at collection, before any test runs entry.main() and installs the guard.
_CPYTHON_WMI_QUERY = getattr(platform, "_wmi_query", None)

needs_wmi = pytest.mark.skipif(
    sys.platform != "win32" or sys.version_info < (3, 12),
    reason="CPython reads platform facts through _wmi only on Windows, 3.12+",
)


@pytest.fixture
def cold_platform(monkeypatch):
    """Empty platform's caches, and put them back after the test."""
    monkeypatch.setattr(platform, "_wmi_query", _CPYTHON_WMI_QUERY)
    monkeypatch.setattr(platform, "_uname_cache", None)
    monkeypatch.setattr(platform, "_platform_cache", {})


@pytest.fixture
def wmi_calls(monkeypatch):
    """Record every query that reaches the native ``_wmi`` module, and fail it."""
    import _wmi

    calls = []

    def _exec_query(query):
        calls.append(query)
        raise AssertionError(f"WMI was queried: {query}")

    monkeypatch.setattr(_wmi, "exec_query", _exec_query)
    return calls


def _ask_everything():
    platform.uname()
    platform.system()
    platform.version()
    platform.release()
    platform.machine()
    platform.processor()
    platform.win32_ver()
    platform.platform()


@needs_wmi
def test_without_the_guard_platform_queries_wmi(cold_platform, wmi_calls):
    with pytest.raises(AssertionError):
        platform.version()
    assert wmi_calls


@needs_wmi
def test_with_the_guard_no_platform_question_reaches_wmi(cold_platform, wmi_calls):
    platform_guard.avoid_wmi()
    _ask_everything()
    assert wmi_calls == []
    assert platform.system() == "Windows"
    assert platform.machine()
    assert platform.version().count(".") == 2


@pytest.fixture
def guarded(cold_platform):
    platform_guard.avoid_wmi()


@needs_wmi
def test_a_prompt_build_and_system_facts_never_reach_wmi(
    cold_platform, wmi_calls, guarded, flagship
):
    """What the sidecar asks per turn (the prompt's ENVIRONMENT block) and on a
    memory refresh (system facts), from an agent built in a guarded process."""
    from gaia.agents.base.system_context import collect_system_info

    agent, _model, _doc = flagship
    prompt = agent._get_system_prompt()
    facts = collect_system_info()
    assert wmi_calls == []
    assert "**ENVIRONMENT:** Windows (" in prompt
    assert any(f["content"].startswith("Operating system: Windows") for f in facts)


def test_the_guard_makes_wmi_look_unavailable(cold_platform):
    platform_guard.avoid_wmi()
    with pytest.raises(OSError):
        platform._wmi_query("OS", "Version")
    platform_guard.avoid_wmi()  # idempotent


def test_a_python_without_the_hook_fails_loudly(monkeypatch):
    monkeypatch.delattr(platform, "_wmi_query")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "version_info", (3, 12, 10))
    with pytest.raises(RuntimeError, match="platform._wmi_query is gone"):
        platform_guard.avoid_wmi()


def test_entry_guards_before_anything_else(monkeypatch):
    order = []
    monkeypatch.setattr(platform_guard, "avoid_wmi", lambda: order.append("guard"))
    monkeypatch.setattr(
        "gaia.login_path.apply_login_path", lambda: order.append("path")
    )
    monkeypatch.setattr(
        entry,
        "selects_http",
        lambda argv: order.append("dispatch") or (_ for _ in ()).throw(SystemExit(0)),
    )
    with pytest.raises(SystemExit):
        entry.main([])
    assert order[0] == "guard"


def test_the_sidecar_module_guards_before_importing_the_agent():
    """``uvicorn server:app`` (dev mode) and the frozen binary import this file,
    never ``entry.main``, before the agent stack."""
    tree = ast.parse(PACKAGING_SERVER.read_text(encoding="utf-8"))
    order = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            if getattr(node.value.func, "id", "") == "avoid_wmi":
                order.append("guard")
        elif isinstance(node, ast.ImportFrom) and node.module in (
            "gaia.logger",
            "gaia_agent.server",
        ):
            order.append(node.module)
    assert order == ["guard", "gaia.logger", "gaia_agent.server"]
