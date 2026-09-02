"""`gaia chat --no-learned-skills` reaches ``Agent._learned_skills_enabled``.

Argparse accepting the flag proves nothing — the flag was documented for months
while no code read it. These tests drive the real ``chat`` handler with a stub
chat wheel and assert the attribute the render path actually consults.
"""

import sys
import types
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.cli import build_parser, run_cli


class _StubChatAgent:
    """Stands in for the gaia-agent-chat wheel's ChatAgent.

    Inherits the flag's default from the core base ``Agent`` rather than
    restating it, so the test fails if that default ever flips.
    """

    _learned_skills_enabled = Agent._learned_skills_enabled

    def __init__(self, config):
        self.config = config
        self.current_session = object()
        self.listed = False

    def list_tools(self, verbose=False):
        self.listed = True

    def stop_watching(self):
        pass


@pytest.fixture
def stub_chat_wheel(monkeypatch):
    """Install a fake ``gaia_agent_chat`` so the handler runs without a model."""
    built = []

    class _Config:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Agent(_StubChatAgent):
        def __init__(self, config):
            super().__init__(config)
            built.append(self)

    agent_mod = types.ModuleType("gaia_agent_chat.agent")
    agent_mod.ChatAgent = _Agent
    agent_mod.ChatAgentConfig = _Config
    app_mod = types.ModuleType("gaia_agent_chat.app")
    app_mod.interactive_mode = lambda agent: None
    pkg = types.ModuleType("gaia_agent_chat")

    monkeypatch.setitem(sys.modules, "gaia_agent_chat", pkg)
    monkeypatch.setitem(sys.modules, "gaia_agent_chat.agent", agent_mod)
    monkeypatch.setitem(sys.modules, "gaia_agent_chat.app", app_mod)
    return built


# ``run_cli`` goes through ``asyncio.run``, whose Windows self-pipe trips the
# unit-test socket guard. The stub wheel below is what keeps these tests off the
# network — no real client is ever constructed.
needs_event_loop = pytest.mark.allow_network


def _run_chat(**overrides):
    """Invoke the real ``chat`` action, stopping right after agent construction.

    ``model`` and ``device`` are explicit so device resolution never probes
    Lemonade; ``list_tools`` makes the handler return before any inference.
    """
    kwargs = {
        "model": "stub-model",
        "device": "cpu",
        "base_url": "http://stub.invalid/api/v1",
        "no_lemonade_check": True,
        "list_tools": True,
        "debug": False,
    }
    kwargs.update(overrides)
    run_cli("chat", **kwargs)


def test_parser_accepts_flag():
    args = build_parser().parse_args(["chat", "--no-learned-skills"])
    assert args.no_learned_skills is True


def test_parser_default_is_off():
    args = build_parser().parse_args(["chat"])
    assert args.no_learned_skills is False


@needs_event_loop
def test_flag_disables_learned_skills_on_the_agent(stub_chat_wheel):
    _run_chat(no_learned_skills=True)

    assert len(stub_chat_wheel) == 1
    agent = stub_chat_wheel[0]
    assert agent._learned_skills_enabled is False
    assert agent.listed is True


@needs_event_loop
def test_without_flag_learned_skills_stay_enabled(stub_chat_wheel):
    _run_chat()

    assert len(stub_chat_wheel) == 1
    assert stub_chat_wheel[0]._learned_skills_enabled is True


@needs_event_loop
def test_render_path_honours_the_disabled_flag(stub_chat_wheel):
    """The attribute the flag sets is the one the render path gates on.

    The agent is otherwise fully learnable — memory store present, not
    incognito — so ``_learned_skills_enabled`` is the only thing that can turn
    the overlay off. Without this, the flag could land on a misspelled
    attribute and every other assertion here would still pass.
    """
    from gaia.agents.base.agent import effective_skill_body

    _run_chat(no_learned_skills=True)
    agent = stub_chat_wheel[0]
    agent._memory_store = object()
    agent._incognito = False
    agent.learned_skills_enabled = types.MethodType(Agent.learned_skills_enabled, agent)

    assert agent.learned_skills_enabled() is False
    skill = types.SimpleNamespace(name="demo", body="authored body")
    assert effective_skill_body(agent, skill) == "authored body"

    # Same agent, flag cleared: resolution now reaches the (stub) store.
    agent._learned_skills_enabled = True
    assert agent.learned_skills_enabled() is True


def test_ui_combination_fails_loudly(capsys, monkeypatch):
    """The UI builds its own agents, so the flag must not silently no-op."""
    from gaia import cli

    monkeypatch.setattr(sys, "argv", ["gaia", "chat", "--ui", "--no-learned-skills"])
    with patch.object(cli, "_launch_agent_ui") as launch:
        with pytest.raises(SystemExit) as exc:
            cli.main()

    assert exc.value.code == 1
    launch.assert_not_called()
    assert "--no-learned-skills" in capsys.readouterr().err


@needs_event_loop
def test_the_env_var_reaches_agents_no_cli_flag_can(stub_chat_wheel, monkeypatch):
    """``GAIA_NO_LEARNED_SKILLS`` is the off-switch for the agent that learns.

    ``--no-learned-skills`` only exists on ``gaia chat``, which builds a
    ChatAgent. The flagship — the only agent that registers
    ``remember_skill_lesson`` — runs as a daemon sidecar and behind the UI
    server, where no chat flag reaches it. The env var is read at call time so
    it also holds for an agent constructed before it was set.
    """
    from gaia.agents.base.agent import effective_skill_body

    _run_chat()
    agent = stub_chat_wheel[0]
    agent._memory_store = object()
    agent._incognito = False
    agent.learned_skills_enabled = types.MethodType(Agent.learned_skills_enabled, agent)

    assert agent._learned_skills_enabled is True
    assert agent.learned_skills_enabled() is True, "precondition: overlay is on"

    monkeypatch.setenv("GAIA_NO_LEARNED_SKILLS", "1")
    assert agent.learned_skills_enabled() is False
    skill = types.SimpleNamespace(name="demo", body="authored body")
    assert effective_skill_body(agent, skill) == "authored body"

    # An unset or falsy value must not disable it by accident.
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("GAIA_NO_LEARNED_SKILLS", value)
        assert agent.learned_skills_enabled() is True, f"{value!r} disabled the overlay"

    monkeypatch.delenv("GAIA_NO_LEARNED_SKILLS")
    assert agent.learned_skills_enabled() is True
