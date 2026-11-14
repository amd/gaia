# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia chat` where the agent ships as its own binary: through the daemon relay.

The frozen gaia the apps ship carries no gaia_agent package, so `gaia chat`
talks to the agent's binary the way the Agent UI does.
"""

import pytest

from gaia import cli


class _Outcome:
    exit_code = 0


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def run_query(agent_id, query, **kwargs):
        seen.append((agent_id, query, kwargs))
        return _Outcome()

    monkeypatch.setattr("gaia.daemon.agent_query.run_query", run_query)
    return seen


def test_one_query_runs_on_the_agent_binary_in_this_folder(
    calls, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    assert cli._gaia_chat_relay({"query": "hi", "model": "m"}) == 0
    [(agent_id, query, kwargs)] = calls
    assert (agent_id, query, kwargs["model"]) == ("gaia", "hi", "m")
    assert kwargs["working_directory"] == str(tmp_path)


def test_the_repl_keeps_one_session_across_turns(calls, monkeypatch):
    replies = iter(["first", "second", "/quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    assert cli._gaia_chat_relay({}) == 0
    assert [q for _, q, _ in calls] == ["first", "second"]
    sessions = {kw["session_id"] for _, _, kw in calls}
    assert len(sessions) == 1 and None not in sessions


@pytest.mark.parametrize(
    "key, flag",
    [("index", "--index"), ("use_claude", "--use-claude"), ("device", "--device")],
)
def test_options_only_an_in_process_agent_has_are_refused(calls, capsys, key, flag):
    assert cli._gaia_chat_relay({"query": "hi", key: ["x"]}) == 1
    assert flag in capsys.readouterr().err
    assert calls == []
