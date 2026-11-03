# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The deprecated gaia-agent-chat shim keeps every old import working.

ChatAgent moved into the flagship package (#6099). For one release this
package re-exports it: each old submodule must BE the new module, so a
``patch("gaia_agent_chat.agent.X")`` written against the old path still
patches what the agent runs.
"""

import importlib
import re
import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest

CHAT_ROOT = Path(__file__).resolve().parents[1]

MOVED = {
    "agent": "gaia_agent.chat_agent",
    "profiles": "gaia_agent.profiles",
    "tool_bundles": "gaia_agent.tool_bundles",
    "session": "gaia_agent.session",
    "python_jobs": "gaia_agent.python_jobs",
    "python_runner": "gaia_agent.python_runner",
    "tool_requests": "gaia_agent.tool_requests",
}


def _fresh_import(name):
    """Import *name* as if for the first time in this process."""
    for mod in [m for m in sys.modules if m.split(".")[0] == "gaia_agent_chat"]:
        sys.modules.pop(mod)
    return importlib.import_module(name)


def test_importing_the_package_warns_that_it_is_deprecated():
    with pytest.warns(DeprecationWarning, match=r"gaia_agent\.chat_agent"):
        _fresh_import("gaia_agent_chat")


@pytest.mark.parametrize("old, new", sorted(MOVED.items()))
def test_each_old_submodule_is_the_new_module(old, new):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = _fresh_import(f"gaia_agent_chat.{old}")
    target = importlib.import_module(new)
    assert legacy is target
    assert sys.modules[f"gaia_agent_chat.{old}"] is target
    assert getattr(sys.modules["gaia_agent_chat"], old) is target


def test_patching_the_old_path_patches_what_the_agent_runs():
    import gaia_agent.chat_agent as chat_agent

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        _fresh_import("gaia_agent_chat.agent")
    sentinel = object()
    with patch("gaia_agent_chat.agent.run_python_job", sentinel):
        assert chat_agent.run_python_job is sentinel
    assert chat_agent.run_python_job is not sentinel


def test_lazy_reexports_are_the_flagship_classes():
    from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = _fresh_import("gaia_agent_chat")
    assert legacy.ChatAgent is ChatAgent
    assert legacy.ChatAgentConfig is ChatAgentConfig
    assert legacy.ChatAgentLite.__module__ == "gaia_agent_chat.lite_agent"
    with pytest.raises(AttributeError):
        legacy.build_chat  # pylint: disable=pointless-statement


def test_app_reexports_interactive_mode():
    from gaia_agent.interactive import interactive_mode

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        app = _fresh_import("gaia_agent_chat.app")
    assert app.interactive_mode is interactive_mode


def test_package_registers_no_agent_ids():
    """chat/doc/file resolve to the flagship through registry aliases; an entry
    point here would register them again and shadow the alias."""
    pyproject = (CHAT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\[project\.entry-points", pyproject)


def test_shim_requires_the_flagship_it_reexports():
    pyproject = (CHAT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    manifest = (CHAT_ROOT / "gaia-agent.yaml").read_text(encoding="utf-8")
    floor = re.compile(r'"gaia-agent-gaia>=([0-9.]+)"')
    assert floor.search(pyproject), "pyproject.toml must require gaia-agent-gaia"
    assert floor.search(manifest), "gaia-agent.yaml must require gaia-agent-gaia"
    assert floor.search(pyproject).group(1) == floor.search(manifest).group(1)
