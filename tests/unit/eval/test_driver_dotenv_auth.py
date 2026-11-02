# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A ``.env``-only ANTHROPIC_API_KEY must not disable subscription auth (#3367)."""

import json
import logging
import os
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

import gaia
from gaia.eval import runner
from gaia.eval.claude_outage import (
    QUOTA,
    claude_account,
    driver_api_key,
    driver_api_key_for,
    driver_environment,
    switch_to_backup_account,
)

KEY = "ANTHROPIC_API_KEY"


@pytest.fixture
def key_env(monkeypatch):
    """Set the key in the shell (pre-.env snapshot), via a .env, or not at all."""
    monkeypatch.setattr(runner, "_warned_dotenv_api_key", False)

    def apply(shell=None, current=None):
        if shell is None:
            monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, KEY, raising=False)
        else:
            monkeypatch.setitem(gaia._PRE_DOTENV_ENVIRON, KEY, shell)
        if current is None:
            monkeypatch.delenv(KEY, raising=False)
        else:
            monkeypatch.setenv(KEY, current)

    return apply


def _launch(tmp_path):
    run = MagicMock(
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "structured_output": {
                        "scenario_id": "s1",
                        "status": "PASS",
                        "turns": [],
                        "overall_score": 10,
                    }
                }
            ),
            stderr="",
        ),
    )
    with patch.object(runner.shutil, "which", return_value="/test/claude"), patch.object(
        runner, "_run_driver", run
    ):
        runner.run_scenario_subprocess(
            tmp_path / "s1.yaml",
            {"id": "s1", "category": "tool_selection", "setup": {}, "turns": []},
            tmp_path,
            "http://localhost:4200",
            "test-model",
            "1.00",
            30,
        )
    return run.call_args.args[0], run.call_args.kwargs["env"]


def test_dotenv_only_key_drops_bare_and_the_key(tmp_path, key_env, caplog):
    key_env(shell=None, current="sk-ant-stale-from-dotenv")
    with caplog.at_level(logging.WARNING, logger="gaia.eval.runner"):
        cmd, env = _launch(tmp_path)
    assert "--bare" not in cmd
    # Not passing --bare is not enough: the CLI prefers an inherited key to OAuth.
    assert env is not None and KEY not in env
    # Everything else is still inherited.
    assert env.get("PATH") == os.environ.get("PATH")
    assert any(".env" in r.getMessage() for r in caplog.records)
    assert "sk-ant-stale-from-dotenv" not in caplog.text


def test_shell_key_keeps_bare_and_inherits(tmp_path, key_env):
    key_env(shell="sk-ant-shell", current="sk-ant-shell")
    cmd, env = _launch(tmp_path)
    assert cmd[:3] == ["/test/claude", "-p", "--bare"]
    assert env is None


@pytest.mark.parametrize(
    "shell,current",
    [
        (None, None),  # no key anywhere: plain OAuth
        ("", ""),  # the documented `ANTHROPIC_API_KEY=` workaround
        ("   ", "   "),  # whitespace is not a key
        ("sk-ant-shell", None),  # unset in-process since startup
        ("sk-ant-shell", ""),
    ],
)
def test_no_usable_key_means_no_bare(tmp_path, key_env, shell, current):
    key_env(shell=shell, current=current)
    cmd, env = _launch(tmp_path)
    assert "--bare" not in cmd
    assert env is None


def test_blank_shell_key_does_not_bless_a_dotenv_value(tmp_path, key_env):
    # load_dotenv never overrides a set variable, but an in-process rewrite could.
    key_env(shell="", current="sk-ant-from-somewhere")
    cmd, env = _launch(tmp_path)
    assert "--bare" not in cmd
    assert KEY not in env


def test_warning_is_logged_once_per_process(key_env, caplog):
    key_env(shell=None, current="sk-ant-dotenv")
    with caplog.at_level(logging.WARNING, logger="gaia.eval.runner"):
        runner._driver_auth()
        runner._driver_auth()
    assert sum(".env" in r.getMessage() for r in caplog.records) == 1


def test_explicit_environment_mapping_can_supply_a_key(key_env):
    key_env(shell=None, current="sk-ant-dotenv")

    assert driver_api_key_for({KEY: "sk-ant-explicit"}) == "sk-ant-explicit"
    assert driver_environment({KEY: "sk-ant-explicit"})[KEY] == "sk-ant-explicit"
    assert KEY not in driver_environment({KEY: "sk-ant-dotenv"})


def test_account_label_ignores_a_dotenv_key(monkeypatch, key_env):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("GAIA_CLAUDE_ACCOUNT", raising=False)
    key_env(shell=None, current="sk-ant-dotenv")
    assert driver_api_key() == ""
    assert "API key" not in claude_account()
    key_env(shell="sk-ant-shell", current="sk-ant-shell")
    assert driver_api_key() == "sk-ant-shell"
    assert "API key" in claude_account()


def test_backup_switch_is_not_blocked_by_a_dotenv_key(monkeypatch, key_env):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok-primary")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "tok-backup")
    monkeypatch.setenv("GAIA_CLAUDE_ACCOUNT", "primary")
    key_env(shell=None, current="sk-ant-dotenv")
    switched = switch_to_backup_account({"kind": QUOTA})
    assert switched and switched["to"] == "secondary"
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-backup"


def test_run_driver_passes_env_to_popen(tmp_path):
    popen = MagicMock()
    popen.return_value.communicate.return_value = ("", "")
    popen.return_value.returncode = 0
    with patch.object(runner.subprocess, "Popen", popen):
        runner._run_driver(
            ["claude"], input="x", timeout=5, cwd=str(tmp_path), env={"A": "1"}
        )
    assert popen.call_args.kwargs["env"] == {"A": "1"}
