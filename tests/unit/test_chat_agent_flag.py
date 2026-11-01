# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia chat --agent <id>`` runs a registered agent through AgentRegistry (#2242).

Each test gets its own GAIA_HOME holding a real custom agent at
``<home>/agents/<id>/agent.py``, discovered by the same registry
``gaia agent status`` reads — no registry mocks.
"""

from __future__ import annotations

import io
import json
import sys
import textwrap
from pathlib import Path

import pytest

_AGENT_SRC = textwrap.dedent("""
    import json
    import os

    from gaia.agents.base.agent import Agent


    class {cls}(Agent):
        AGENT_ID = "{agent_id}"
        AGENT_NAME = "{cls}"
        AGENT_DESCRIPTION = "test fixture"
        CONVERSATION_STARTERS = []

        def __init__(self, model_id=None, base_url=None, max_steps=None,
                     silent_mode=False, debug=False, streaming=False,
                     show_prompts=False, show_stats=False, use_claude=False,
                     claude_model=None, device=None):
            if {broken}:
                raise RuntimeError("model weights missing")
            self._log("init", dict(
                model_id=model_id, base_url=base_url, max_steps=max_steps,
                silent_mode=silent_mode, debug=debug, streaming=streaming,
                show_prompts=show_prompts, show_stats=show_stats,
                use_claude=use_claude, device=device))

        @staticmethod
        def _log(kind, payload):
            path = os.environ["GAIA_TEST_AGENT_LOG"]
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({{"kind": kind, "payload": payload}}))
                f.write(chr(10))

        def _register_tools(self):
            pass

        def _get_system_prompt(self):
            return "fixture"

        def list_tools(self, verbose=True):
            self._log("list_tools", {{}})

        def process_query(self, query, trace=False, **_):
            self._log("query", {{"query": query, "trace": trace}})
            return {{"status": "{status}", "result": "ok"}}
    """)


def _write_agent(home: Path, agent_id: str, *, broken=False, status="success"):
    d = home / "agents" / agent_id
    d.mkdir(parents=True)
    cls = "".join(p.capitalize() for p in agent_id.split("-")) + "Agent"
    (d / "agent.py").write_text(
        _AGENT_SRC.format(cls=cls, agent_id=agent_id, broken=broken, status=status),
        encoding="utf-8",
    )


@pytest.fixture
def gaia_home(tmp_path, monkeypatch):
    home = tmp_path / "gaia_home"
    home.mkdir()
    monkeypatch.setenv("GAIA_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_FILE", raising=False)
    monkeypatch.setenv("GAIA_TEST_AGENT_LOG", str(tmp_path / "agent_log.jsonl"))
    return home


def _events(home: Path):
    log = home.parent / "agent_log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _run_main(argv, monkeypatch, stdin=None):
    from gaia import cli

    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


_BASE = ["chat", "--no-lemonade-check"]


class TestRunsRegisteredAgent:
    def test_one_shot_query_reaches_the_custom_agent(self, gaia_home, monkeypatch):
        _write_agent(gaia_home, "jarvis")
        code = _run_main(
            [*_BASE, "--agent", "jarvis", "-q", "hi", "--model", "m1"], monkeypatch
        )
        assert code == 0
        events = _events(gaia_home)
        assert [e["kind"] for e in events] == ["init", "query"]
        init = events[0]["payload"]
        assert init["model_id"] == "m1"
        assert init["silent_mode"] is True and init["debug"] is False
        assert init["max_steps"] is None
        assert isinstance(init["base_url"], str) and init["base_url"]
        assert events[1]["payload"] == {"query": "hi", "trace": False}

    def test_config_default_model_does_not_override_the_agents_own(
        self, gaia_home, monkeypatch
    ):
        """Matches the Agent UI: only an explicit --model reaches a custom agent."""
        _write_agent(gaia_home, "jarvis")
        cfg = gaia_home / "config.json"
        cfg.write_text(json.dumps({"default_model": "cfg-model"}), encoding="utf-8")
        argv = [*_BASE, "--config", str(cfg), "--agent", "jarvis", "-q", "hi"]
        assert _run_main(argv, monkeypatch) == 0
        assert _events(gaia_home)[0]["payload"]["model_id"] is None

    def test_explicit_model_wins_over_config_default(self, gaia_home, monkeypatch):
        _write_agent(gaia_home, "jarvis")
        cfg = gaia_home / "config.json"
        cfg.write_text(json.dumps({"default_model": "cfg-model"}), encoding="utf-8")
        argv = [*_BASE, "--config", str(cfg), "--agent", "jarvis", "-q", "hi"]
        assert _run_main([*argv, "--model", "m2"], monkeypatch) == 0
        assert _events(gaia_home)[0]["payload"]["model_id"] == "m2"

    def test_cli_flags_reach_the_constructor(self, gaia_home, monkeypatch):
        _write_agent(gaia_home, "jarvis")
        code = _run_main(
            [
                *_BASE,
                "--agent",
                "jarvis",
                "-q",
                "hi",
                "--max-steps",
                "7",
                "--debug",
                "--stream",
                "--show-prompts",
                "--trace",
            ],
            monkeypatch,
        )
        assert code == 0
        init, query = _events(gaia_home)
        assert init["payload"]["max_steps"] == 7
        assert init["payload"]["debug"] is True
        assert init["payload"]["silent_mode"] is False
        assert init["payload"]["streaming"] is True
        assert init["payload"]["show_prompts"] is True
        assert query["payload"]["trace"] is True

    def test_failed_query_exits_non_zero(self, gaia_home, monkeypatch):
        _write_agent(gaia_home, "jarvis", status="error")
        assert _run_main([*_BASE, "--agent", "jarvis", "-q", "hi"], monkeypatch) == 1

    def test_interactive_loop_until_quit(self, gaia_home, monkeypatch):
        _write_agent(gaia_home, "jarvis")
        code = _run_main(
            [*_BASE, "--agent", "jarvis"], monkeypatch, stdin="first\n\n  \n/quit\n"
        )
        assert code == 0
        queries = [
            e["payload"]["query"] for e in _events(gaia_home) if e["kind"] == "query"
        ]
        assert queries == ["first"]

    def test_interactive_eof_exits_cleanly(self, gaia_home, monkeypatch):
        _write_agent(gaia_home, "jarvis")
        assert _run_main([*_BASE, "--agent", "jarvis"], monkeypatch, stdin="") == 0
        assert [e["kind"] for e in _events(gaia_home)] == ["init"]

    def test_list_tools_does_not_run_a_query(self, gaia_home, monkeypatch):
        _write_agent(gaia_home, "jarvis")
        code = _run_main([*_BASE, "--agent", "jarvis", "--list-tools"], monkeypatch)
        assert code == 0
        assert [e["kind"] for e in _events(gaia_home)] == ["init", "list_tools"]


class TestFailsLoudly:
    def test_unknown_id_lists_registered_ids(self, gaia_home, monkeypatch, capsys):
        _write_agent(gaia_home, "jarvis")
        code = _run_main([*_BASE, "--agent", "nope", "-q", "hi"], monkeypatch)
        assert code == 2
        err = capsys.readouterr().err
        assert "No agent registered as 'nope'" in err
        assert "jarvis" in err
        assert _events(gaia_home) == []

    def test_unknown_id_list_matches_agent_status(self, gaia_home, monkeypatch, capsys):
        """The run surface and `gaia agent status` read the same registry."""
        from gaia.cli_agent import _build_registry
        from gaia.hub import lifecycle

        _write_agent(gaia_home, "jarvis")
        _write_agent(gaia_home, "friday")
        _run_main([*_BASE, "--agent", "nope", "-q", "hi"], monkeypatch)
        err = capsys.readouterr().err
        listed = err.split("Registered agents: ", 1)[1].split(".\n", 1)[0]
        listed_ids = set(listed.split(", "))
        status_ids = set(lifecycle.status_all(registry=_build_registry()))
        assert {"jarvis", "friday"} <= listed_ids
        assert listed_ids <= status_ids

    def test_registered_but_broken_agent(self, gaia_home, monkeypatch, capsys):
        _write_agent(gaia_home, "jarvis", broken=True)
        code = _run_main([*_BASE, "--agent", "jarvis", "-q", "hi"], monkeypatch)
        assert code == 1
        err = capsys.readouterr().err
        assert "'jarvis' is registered but failed to start" in err
        assert "model weights missing" in err
        assert "gaia agent health jarvis" in err

    def test_agent_that_failed_to_load_is_unknown(self, gaia_home, monkeypatch, capsys):
        d = gaia_home / "agents" / "jarvis"
        d.mkdir(parents=True)
        (d / "agent.py").write_text("this is not python(", encoding="utf-8")
        code = _run_main([*_BASE, "--agent", "jarvis", "-q", "hi"], monkeypatch)
        assert code == 2
        assert "No agent registered as 'jarvis'" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "flag",
        [
            ["--index", "doc.pdf"],
            ["--watch", "."],
            ["--chunk-size", "600"],
            ["--max-chunks", "5"],
            ["--allowed-paths", "."],
            ["--max-indexed-files", "5"],
            ["--max-total-chunks", "5"],
            ["--mcp-tool-limit", "5"],
            ["--no-learned-skills"],
            ["--ui"],
        ],
    )
    def test_flagship_only_flags_are_rejected(
        self, gaia_home, monkeypatch, capsys, flag
    ):
        _write_agent(gaia_home, "jarvis")
        code = _run_main([*_BASE, "--agent", "jarvis", *flag, "-q", "hi"], monkeypatch)
        assert code == 2
        assert flag[0] in capsys.readouterr().err
        assert _events(gaia_home) == []

    def test_flagship_only_flag_at_its_default_is_allowed(self, gaia_home, monkeypatch):
        _write_agent(gaia_home, "jarvis")
        code = _run_main(
            [*_BASE, "--agent", "jarvis", "--chunk-size", "500", "-q", "hi"],
            monkeypatch,
        )
        assert code == 0


class TestFlagshipPathUnchanged:
    @pytest.mark.parametrize("agent", [None, "gaia"])
    def test_default_and_gaia_skip_the_registry_runner(self, monkeypatch, agent):
        import asyncio

        from gaia import cli

        called = []
        monkeypatch.setattr(cli, "_run_registered_agent", lambda *a: called.append(a))
        monkeypatch.setitem(sys.modules, "gaia_agent.agent", None)
        kwargs = {"no_lemonade_check": True, "query": "hi"}
        if agent:
            kwargs["agent"] = agent
        with pytest.raises(RuntimeError, match="GAIA agent is not installed"):
            asyncio.run(cli.async_main("chat", **kwargs))
        assert called == []

    def test_gaia_with_flagship_flags_is_not_rejected(self):
        import argparse

        from gaia import cli

        cli._reject_flagship_only_flags(
            argparse.Namespace(agent="gaia", index=["doc.pdf"], chunk_size=900)
        )
