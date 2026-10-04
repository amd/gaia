# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An undeclared ``GAIA_SKILL_SET`` stops startup instead of being dropped (#3200).

The sidecar's warm-up swallows agent-build errors, so the check has to run in
the transport before it binds a port or opens the stdio wire.
"""

from __future__ import annotations

import io

import pytest

pytest.importorskip("gaia_agent")

import uvicorn  # noqa: E402
from gaia_agent import server as server_mod  # noqa: E402
from gaia_agent import stdio as stdio_mod  # noqa: E402
from gaia_agent.agent import (  # noqa: E402
    SKILL_SET_ENV,
    GaiaAgent,
    GaiaAgentConfig,
    check_skill_set_selection,
)

from gaia.skills.errors import SkillSetError  # noqa: E402
from gaia.skills.sets import SkillRef, SkillSets  # noqa: E402

_MANIFEST_WITH_SETS = """\
skills:
  - gaia-voice
skill_sets:
  research:
    - research-report
  documents:
    - document-brief
default_skill_set: documents
"""


@pytest.fixture
def manifest_with_sets(tmp_path, monkeypatch):
    path = tmp_path / "gaia-agent.yaml"
    path.write_text(_MANIFEST_WITH_SETS, encoding="utf-8")
    monkeypatch.setattr(GaiaAgent, "SKILL_MANIFEST", str(path))
    return path


@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(
        uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs))
    )
    return calls


def test_undeclared_env_set_raises_naming_the_valid_sets(
    manifest_with_sets, monkeypatch
):
    monkeypatch.setenv(SKILL_SET_ENV, "reserch")
    with pytest.raises(SkillSetError) as info:
        check_skill_set_selection()
    message = str(info.value)
    assert "GAIA_SKILL_SET='reserch'" in message
    assert "Valid sets: research, documents" in message


def test_any_env_set_raises_while_the_shipped_manifest_declares_none(monkeypatch):
    monkeypatch.setenv(SKILL_SET_ENV, "research")
    with pytest.raises(SkillSetError, match="declares no 'skill_sets:' block"):
        check_skill_set_selection()


def test_declared_env_set_passes(manifest_with_sets, monkeypatch):
    monkeypatch.setenv(SKILL_SET_ENV, "research")
    check_skill_set_selection()


def test_unset_env_passes(monkeypatch):
    monkeypatch.delenv(SKILL_SET_ENV, raising=False)
    check_skill_set_selection()


def test_config_value_is_checked_too(manifest_with_sets, monkeypatch):
    monkeypatch.delenv(SKILL_SET_ENV, raising=False)
    with pytest.raises(SkillSetError, match="GaiaAgentConfig.skill_set='nope'"):
        check_skill_set_selection(GaiaAgentConfig(skill_set="nope"))


def test_agent_build_no_longer_drops_the_env_value(monkeypatch):
    """The agent itself refuses the value, not only the transport pre-check."""
    monkeypatch.setenv(SKILL_SET_ENV, "research")
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig()
    agent._requested_skill_set = None
    # Always-on skills but no sets: exactly what the shipped manifest declares,
    # and the shape that used to discard the hook's answer.
    agent._skill_sets = SkillSets(always=(SkillRef(name="gaia-voice"),))
    with pytest.raises(SkillSetError, match="GAIA_SKILL_SET='research'"):
        agent.load_skill_set()


def test_agent_refuses_the_env_value_when_no_manifest_is_found(monkeypatch):
    """An unpackaged checkout has no manifest; the value must still be refused."""
    monkeypatch.setenv(SKILL_SET_ENV, "research")
    monkeypatch.setattr(GaiaAgent, "SKILL_MANIFEST", None)
    monkeypatch.setattr(GaiaAgent, "_resolve_skill_manifest", lambda self, *_: None)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig()
    agent._requested_skill_set = None
    agent._skill_sets = None
    with pytest.raises(SkillSetError, match="GAIA_SKILL_SET='research'"):
        agent.load_declared_skills()


def test_no_manifest_and_no_env_loads_nothing(monkeypatch):
    monkeypatch.delenv(SKILL_SET_ENV, raising=False)
    monkeypatch.setattr(GaiaAgent, "SKILL_MANIFEST", None)
    monkeypatch.setattr(GaiaAgent, "_resolve_skill_manifest", lambda self, *_: None)
    agent = GaiaAgent.__new__(GaiaAgent)
    agent.config = GaiaAgentConfig()
    agent._requested_skill_set = None
    agent._skill_sets = None
    assert agent.load_declared_skills() == {}


def test_sidecar_exits_non_zero_before_binding_a_port(served, monkeypatch, capsys):
    monkeypatch.setenv(SKILL_SET_ENV, "research")
    assert server_mod.serve_http(["--port", "8149"]) != 0
    assert served == [], "uvicorn was started despite an undeclared skill set"
    assert "GAIA_SKILL_SET='research'" in capsys.readouterr().err


def test_sidecar_starts_when_env_is_unset(served, monkeypatch):
    monkeypatch.delenv(SKILL_SET_ENV, raising=False)
    assert server_mod.serve_http(["--port", "8149"]) == 0
    assert len(served) == 1


def test_stdio_exits_non_zero_before_building_the_agent(monkeypatch):
    monkeypatch.setenv(SKILL_SET_ENV, "research")

    def _never(*_args, **_kwargs):
        raise AssertionError("GaiaAgent was built despite an undeclared skill set")

    monkeypatch.setattr(GaiaAgent, "__init__", _never)
    out = io.StringIO()
    monkeypatch.setattr(stdio_mod.sys, "stdout", out)
    monkeypatch.setattr(stdio_mod.sys, "stdin", io.StringIO(""))
    assert stdio_mod.main([]) == 1
    assert "GAIA_SKILL_SET='research'" in out.getvalue()
