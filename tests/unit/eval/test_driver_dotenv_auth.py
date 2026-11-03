# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The eval driver's credential: subscription, then the env key, then a .env key."""

import json
import os
from types import SimpleNamespace

import pytest

import gaia
from gaia import env as gaia_env
from gaia.eval import claude_credential, runner
from gaia.eval.claude_outage import QUOTA, claude_account, switch_to_backup_account

KEY = "ANTHROPIC_API_KEY"
TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
SHELL_KEY = "sk-ant-shell-SECRET"
DOTENV_KEY = "sk-ant-dotenv-SECRET"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in (KEY, TOKEN, "CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "GAIA_CLAUDE_ACCOUNT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, KEY, raising=False)
    monkeypatch.setattr(gaia_env, "_working_dir_credentials", {})
    monkeypatch.setattr(gaia_env, "_working_dir_credential_files", {})
    monkeypatch.setattr(gaia_env, "_install_tree_dotenv", lambda: None)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "/test/claude")


@pytest.fixture
def logged_out(monkeypatch):
    monkeypatch.setattr(
        claude_credential,
        "_auth_status",
        lambda _bin, _env: {"loggedIn": False, "authMethod": "none"},
    )


def _shell_key(monkeypatch):
    monkeypatch.setitem(gaia._PRE_DOTENV_ENVIRON, KEY, SHELL_KEY)
    monkeypatch.setenv(KEY, SHELL_KEY)


def _dotenv_key(monkeypatch, path="/work/project/.env"):
    monkeypatch.setitem(gaia_env._working_dir_credentials, KEY, DOTENV_KEY)
    monkeypatch.setitem(gaia_env._working_dir_credential_files, KEY, path)


def _launch(tmp_path, mocker):
    run = mocker.patch.object(
        runner,
        "_run_driver",
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


def test_cli_login_beats_an_exported_key(
    tmp_path, mocker, monkeypatch, claude_login, capsys
):
    _shell_key(monkeypatch)
    cmd, env = _launch(tmp_path, mocker)
    assert "--bare" not in cmd
    # Not passing --bare is not enough: the CLI prefers an inherited key to OAuth.
    # Booleans, so a failure never prints the environment.
    dropped_key = env is not None and KEY not in env
    assert dropped_key
    same_path = env.get("PATH") == os.environ.get("PATH")
    assert same_path
    err = capsys.readouterr().err
    assert "Claude eval driver: using the Claude Code CLI login" in err
    assert SHELL_KEY not in err


def test_oauth_token_beats_an_exported_key(tmp_path, mocker, monkeypatch, capsys):
    _shell_key(monkeypatch)
    monkeypatch.setenv(TOKEN, "tok-SECRET")
    cmd, env = _launch(tmp_path, mocker)
    assert "--bare" not in cmd
    kept_token_only = KEY not in env and env[TOKEN] == "tok-SECRET"
    assert kept_token_only
    err = capsys.readouterr().err
    assert "using the Claude Code subscription (CLAUDE_CODE_OAUTH_TOKEN)" in err
    assert "tok-SECRET" not in err and SHELL_KEY not in err


def test_exported_key_when_logged_out(
    tmp_path, mocker, monkeypatch, logged_out, capsys
):
    _shell_key(monkeypatch)
    _dotenv_key(monkeypatch)
    cmd, env = _launch(tmp_path, mocker)
    assert cmd[:3] == ["/test/claude", "-p", "--bare"]
    inherits = env is None
    assert inherits
    err = capsys.readouterr().err
    assert "Claude eval driver: using ANTHROPIC_API_KEY from the environment" in err
    assert SHELL_KEY not in err and DOTENV_KEY not in err


def test_dotenv_key_is_last_and_reaches_the_child(
    tmp_path, mocker, monkeypatch, logged_out, capsys
):
    _dotenv_key(monkeypatch)
    cmd, env = _launch(tmp_path, mocker)
    assert "--bare" in cmd
    carries_key = env[KEY] == DOTENV_KEY
    assert carries_key
    err = capsys.readouterr().err
    assert "Claude eval driver: using ANTHROPIC_API_KEY from /work/project/.env" in err
    assert DOTENV_KEY not in err


def test_nothing_usable_raises(tmp_path, mocker, monkeypatch, logged_out):
    monkeypatch.setenv(KEY, "   ")
    with pytest.raises(ValueError, match="No Claude credential"):
        _launch(tmp_path, mocker)


def test_message_is_printed_once_per_process(monkeypatch, claude_login, capsys):
    runner._driver_auth()
    runner._driver_auth()
    assert capsys.readouterr().err.count("Claude eval driver: using") == 1


def test_account_label_names_the_source_not_the_secret(monkeypatch, logged_out):
    _dotenv_key(monkeypatch)
    assert claude_account() == "ANTHROPIC_API_KEY from /work/project/.env"
    _shell_key(monkeypatch)
    monkeypatch.setenv("GAIA_CLAUDE_ACCOUNT", "primary")
    assert claude_account() == (
        "ANTHROPIC_API_KEY from the environment (the primary account)"
    )
    monkeypatch.setenv(TOKEN, "tok-SECRET")
    label = claude_account()
    assert label.startswith("the Claude Code subscription (CLAUDE_CODE_OAUTH_TOKEN)")
    assert "SECRET" not in label


def test_backup_switch_is_not_blocked_by_a_key_the_subscription_beats(monkeypatch):
    monkeypatch.setenv(TOKEN, "tok-primary")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "tok-backup")
    monkeypatch.setenv("GAIA_CLAUDE_ACCOUNT", "primary")
    _shell_key(monkeypatch)
    switched = switch_to_backup_account({"kind": QUOTA})
    assert switched and switched["to"] == "secondary"
    assert os.environ[TOKEN] == "tok-backup"


def test_backup_switch_does_not_apply_to_a_key_run(monkeypatch):
    # No CLI, so the token cannot be used and the run is on the key.
    monkeypatch.setattr(runner.shutil, "which", lambda _name: None)
    monkeypatch.setenv(TOKEN, "tok-primary")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "tok-backup")
    _shell_key(monkeypatch)
    assert switch_to_backup_account({"kind": QUOTA}) is None


def test_run_driver_passes_env_to_popen(mocker, tmp_path):
    popen = mocker.patch.object(runner.subprocess, "Popen")
    popen.return_value.communicate.return_value = ("", "")
    popen.return_value.returncode = 0
    runner._run_driver(
        ["claude"], input="x", timeout=5, cwd=str(tmp_path), env={"A": "1"}
    )
    assert popen.call_args.kwargs["env"] == {"A": "1"}
