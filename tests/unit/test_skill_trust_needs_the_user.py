# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Only the user, from their own terminal, can change which skills are trusted.

The boundary is in the commands themselves: every process an agent tool starts
carries ``GAIA_AGENT_CHILD``, code an agent tool runs in-process runs inside
``agent_tool_running()``, and promote / install / import / trust / relock /
keygen / publish refuse both. However an agent spells the call, it lands here.

Nothing real is trusted: HOME and GAIA_HOME point at an empty temp folder, the
skills under test do not exist, and every hub call is stubbed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import gaia
from gaia.skills.capture import import_bundle, promote_skill
from gaia.skills.drift import relock
from gaia.skills.errors import SkillNotFoundError
from gaia.skills.human_only import (
    AGENT_CHILD_ENV,
    HUMAN_ONLY_MESSAGE,
    SkillHumanOnlyError,
    agent_child_env,
    agent_tool_running,
    started_by_agent,
)
from gaia.skills.install import install_skill
from gaia.skills.manager import SkillManager
from gaia.skills.publish import publish_skill
from gaia.skills.signing import TrustStore, generate_key

SRC = Path(gaia.__file__).resolve().parent.parent


class _NoHub(Exception):
    """Raised by the stub fetcher; install reports it as a failed fetch."""


def _no_hub(*args, **kwargs):
    raise _NoHub()


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".gaia" / "skills").mkdir(parents=True)
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("GAIA_HOME", str(home / ".gaia"))
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.delenv(AGENT_CHILD_ENV, raising=False)
    return home


@pytest.fixture
def manager(home):
    return SkillManager(
        user_skills_root=home / ".gaia" / "skills", include_claude_roots=False
    )


def _trust_changes(manager, root):
    """Each trust-changing call, ready to run against *root*."""
    store = TrustStore(entries={}, path=root / "trusted-keys.json")
    return {
        "promote": lambda: promote_skill("no-such-skill", manager=manager),
        "install --allow-experimental": lambda: install_skill(
            "no-such-skill", manager=manager, fetcher=_no_hub, allow_experimental=True
        ),
        "install --yes": lambda: install_skill(
            "no-such-skill", manager=manager, fetcher=_no_hub, assume_yes=True
        ),
        "import": lambda: import_bundle(
            root / "missing", origin="test", manager=manager
        ),
        "trust": store.save,
        "relock": lambda: relock(root),
        "keygen": lambda: generate_key(root, name="test-key"),
        "publish": lambda: publish_skill(root / "missing", token="t"),
    }


# ---------------------------------------------------------------------------
# The functions refuse an agent, in a child process or in-process
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        "promote",
        "install --allow-experimental",
        "install --yes",
        "import",
        "trust",
        "relock",
        "keygen",
        "publish",
    ],
)
@pytest.mark.parametrize("where", ["agent child", "agent tool"])
def test_an_agent_cannot_change_skill_trust(manager, home, monkeypatch, action, where):
    root = manager.user_root
    before = sorted(p.name for p in root.iterdir())
    call = _trust_changes(manager, root)[action]

    if where == "agent child":
        monkeypatch.setenv(AGENT_CHILD_ENV, "1")
        with pytest.raises(SkillHumanOnlyError, match=HUMAN_ONLY_MESSAGE):
            call()
    else:
        with (
            agent_tool_running(),
            pytest.raises(SkillHumanOnlyError, match=HUMAN_ONLY_MESSAGE),
        ):
            call()

    assert sorted(p.name for p in root.iterdir()) == before


def test_the_user_terminal_still_reaches_the_real_work(manager, home):
    assert not started_by_agent()
    root = manager.user_root
    calls = _trust_changes(manager, root)

    with pytest.raises(SkillNotFoundError):
        calls["promote"]()
    with pytest.raises(SkillNotFoundError, match="Could not fetch"):
        calls["install --allow-experimental"]()
    calls["trust"]()
    assert (root / "trusted-keys.json").is_file()


def test_the_agent_install_tool_path_is_not_a_trust_change(manager):
    """A signed hub install, dangerous grants refused, stays an agent tool."""
    with (
        agent_tool_running(),
        pytest.raises(SkillNotFoundError, match="Could not fetch"),
    ):
        install_skill("no-such-skill", manager=manager, fetcher=_no_hub)


def test_the_tool_context_ends_with_the_tool():
    with agent_tool_running():
        assert started_by_agent()
    assert not started_by_agent()


def test_the_agent_tool_runner_marks_its_tool_bodies():
    from unittest.mock import patch

    from gaia.agents.base.agent import Agent

    seen = []

    class _Probe(Agent):
        def _get_system_prompt(self):
            return "probe"

        def _register_tools(self):
            pass

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _Probe(silent_mode=True, skip_lemonade=True)
    agent._call_tool_bounded(lambda: seen.append(started_by_agent()), {}, "probe")

    assert seen == [True]
    assert not started_by_agent()


# ---------------------------------------------------------------------------
# Agent tools mark the processes they start
# ---------------------------------------------------------------------------


def test_a_shell_command_cannot_clear_the_marker():
    from gaia.agents.tools.shell_tools import _segment_env

    assert _segment_env({AGENT_CHILD_ENV: ""})[AGENT_CHILD_ENV] == "1"
    assert _segment_env({})[AGENT_CHILD_ENV] == "1"


def test_a_python_tool_child_and_its_children_carry_the_marker(tmp_path):
    pytest.importorskip("gaia_agent")
    from gaia_agent.python_jobs import run_python_job

    grandchild = (
        "import os, subprocess, sys\n"
        "print(os.environ.get('GAIA_AGENT_CHILD'))\n"
        "subprocess.run([sys.executable, '-c', "
        "\"import os; print(os.environ.get('GAIA_AGENT_CHILD'))\"])\n"
    )
    job = run_python_job(
        [sys.executable, "-c", grandchild],
        cwd=str(tmp_path),
        env=None,
        timeout=60,
        should_stop=lambda: False,
    )

    assert job.run.stdout.split() == ["1", "1"]


def test_agent_child_env_keeps_the_rest_of_the_environment():
    env = agent_child_env({"PATH": "x"})

    assert env == {"PATH": "x", AGENT_CHILD_ENV: "1"}


# ---------------------------------------------------------------------------
# The CLI refuses a marked child, however it was launched
# ---------------------------------------------------------------------------


def _child_env(home: Path, marked: bool) -> dict:
    env = dict(os.environ)
    env.pop(AGENT_CHILD_ENV, None)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        GAIA_HOME=str(home / ".gaia"),
        LEMONADE_BASE_URL="http://127.0.0.1:1",
        PYTHONPATH=str(SRC),
        PYTHONIOENCODING="utf-8",
    )
    return agent_child_env(env) if marked else env


_CLI = (
    "import sys; sys.argv = ['gaia', *sys.argv[1:]]; from gaia.cli import main; main()"
)

#: Launches that a text scan has missed before; each must still be refused.
LAUNCHES = {
    "python -m gaia.cli": [sys.executable, "-m", "gaia.cli", "skill", "promote", "x"],
    "python -mgaia.cli": [sys.executable, "-mgaia.cli", "skill", "promote", "x"],
    "gaia.cli main()": [sys.executable, "-c", _CLI, "skill", "promote", "x"],
    "runpy": [
        sys.executable,
        "-c",
        "import runpy, sys; sys.argv = ['gaia', 'skill', 'promote', 'x']; "
        "runpy.run_module('gaia.cli', run_name='__main__')",
    ],
    "promote_skill()": [
        sys.executable,
        "-c",
        "import importlib; importlib.import_module('gaia.skills.capture')"
        ".promote_skill('x')",
    ],
    "install --allow-experimental": [
        sys.executable,
        "-c",
        _CLI,
        "skill",
        "install",
        "x",
        "--allow-experimental",
    ],
    "trust add": [
        sys.executable,
        "-c",
        _CLI,
        "skill",
        "trust",
        "add",
        "MCowBQYDK2VwAyEAGb9ECWmEzf6FQbrBZ9w7lshQhqowtrbLDFw4rXAxZuE=",
    ],
}


@pytest.mark.parametrize("launch", sorted(LAUNCHES))
def test_a_marked_child_cannot_change_skill_trust(home, launch):
    done = subprocess.run(
        LAUNCHES[launch],
        env=_child_env(home, marked=True),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=240,
        check=False,
    )

    assert done.returncode != 0
    assert HUMAN_ONLY_MESSAGE in done.stdout + done.stderr
    assert not (home / ".gaia" / "skills" / "trusted-keys.json").exists()


def test_the_users_terminal_can_still_run_promote(home):
    done = subprocess.run(
        LAUNCHES["gaia.cli main()"],
        env=_child_env(home, marked=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=240,
        check=False,
    )

    # Past the trust check: the real promote looked for the skill.
    assert HUMAN_ONLY_MESSAGE not in done.stdout + done.stderr
    assert "No skill named 'x'" in done.stderr
