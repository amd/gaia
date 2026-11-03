# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Judge credential order and the `claude -p` judge client.

Covers the two things a broken judge credential used to get wrong silently:
which credential gets picked (and whether the run says so), and whether a
rejected or empty CLI reply surfaces as an actionable error instead of an
unscored case.
"""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import gaia
from gaia import env as gaia_env
from gaia.eval import claude_credential, judge_client
from gaia.eval.judge_client import (
    ClaudeCliClient,
    make_judge_client,
    probe_judge_credential,
)

ENV_KEY = "sk-ant-api03-ENVSECRET"
DOTENV_KEY = "sk-ant-api03-DOTENVSECRET"
OAUTH = "sk-ant-oat01-OAUTHSECRET"
SECRETS = (ENV_KEY, DOTENV_KEY, OAUTH)

# Captured at import, before the root conftest stubs it for every test.
_REAL_AUTH_STATUS = claude_credential._auth_status


@pytest.fixture(autouse=True)
def _clear_credentials(monkeypatch):
    """No credential is set unless a test sets it.

    `load_env()` at import time can populate one from a developer's local
    `.env`, which would otherwise make selection tests pass for the wrong reason.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, "ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(gaia_env, "_working_dir_credentials", {})
    monkeypatch.setattr(gaia_env, "_working_dir_credential_files", {})
    monkeypatch.setattr(gaia_env, "_install_tree_dotenv", lambda: None)


@pytest.fixture
def fake_claude_bin(monkeypatch):
    monkeypatch.setattr(judge_client.shutil, "which", lambda _: "/usr/bin/claude")


@pytest.fixture
def no_claude_bin(monkeypatch):
    monkeypatch.setattr(judge_client.shutil, "which", lambda _: None)


@pytest.fixture
def logged_out(monkeypatch):
    """A `claude` on PATH that holds no login."""
    monkeypatch.setattr(judge_client.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        claude_credential,
        "_auth_status",
        lambda _bin, _env: {"loggedIn": False, "authMethod": "none"},
    )


def _assert_no_secret(text):
    for secret in SECRETS:
        assert secret not in text
        assert secret[-8:] not in text


def _stub_sdk_client(monkeypatch):
    """Stand in for ClaudeClient so no real Anthropic client is built.

    Announces like the real one, which is what the message tests read.
    """
    sentinel = SimpleNamespace(kwargs=None)

    def build(**kwargs):
        sentinel.kwargs = kwargs
        claude_credential.announce(claude_credential.api_key_credential())
        return sentinel

    monkeypatch.setitem(
        sys.modules, "gaia.eval.claude", SimpleNamespace(ClaudeClient=build)
    )
    return sentinel


PROJECT_DOTENV = "/work/project/.env"


@pytest.fixture
def project_key(monkeypatch):
    """ANTHROPIC_API_KEY supplied only by a working-directory .env."""
    monkeypatch.setitem(
        gaia_env._working_dir_credentials, "ANTHROPIC_API_KEY", DOTENV_KEY
    )
    monkeypatch.setitem(
        gaia_env._working_dir_credential_files, "ANTHROPIC_API_KEY", PROJECT_DOTENV
    )


@pytest.fixture
def exported_key(monkeypatch):
    monkeypatch.setitem(gaia._PRE_DOTENV_ENVIRON, "ANTHROPIC_API_KEY", ENV_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)


# ---------------------------------------------------------------------------
# Credential order: subscription, then the env key, then a .env key
# ---------------------------------------------------------------------------


def test_oauth_token_wins_and_says_so(
    monkeypatch, fake_claude_bin, exported_key, project_key, capsys
):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    _stub_sdk_client(monkeypatch)
    client = make_judge_client(model="claude-sonnet-5")
    assert isinstance(client, ClaudeCliClient)
    assert client.model == "claude-sonnet-5"
    err = capsys.readouterr().err
    assert (
        "Claude judge: using the Claude Code subscription (CLAUDE_CODE_OAUTH_TOKEN)"
        in err
    )
    _assert_no_secret(err)


def test_cli_login_wins_over_an_exported_key(
    monkeypatch, fake_claude_bin, exported_key, capsys
):
    _stub_sdk_client(monkeypatch)
    assert isinstance(make_judge_client(), ClaudeCliClient)
    err = capsys.readouterr().err
    assert "Claude judge: using the Claude Code CLI login" in err
    _assert_no_secret(err)


def test_cli_login_wins_over_a_dotenv_key(monkeypatch, fake_claude_bin, project_key):
    _stub_sdk_client(monkeypatch)
    assert isinstance(make_judge_client(), ClaudeCliClient)


def test_exported_key_when_the_cli_is_logged_out(
    monkeypatch, logged_out, exported_key, capsys
):
    sentinel = _stub_sdk_client(monkeypatch)
    assert make_judge_client(model="claude-opus-5") is sentinel
    err = capsys.readouterr().err
    assert "Claude judge: using ANTHROPIC_API_KEY from the environment" in err
    _assert_no_secret(err)


def test_exported_key_without_a_cli(monkeypatch, no_claude_bin, exported_key, capsys):
    sentinel = _stub_sdk_client(monkeypatch)
    assert make_judge_client() is sentinel
    assert "using ANTHROPIC_API_KEY from the environment" in capsys.readouterr().err


def test_env_key_beats_a_dotenv_key(
    monkeypatch, logged_out, exported_key, project_key, capsys
):
    _stub_sdk_client(monkeypatch)
    make_judge_client()
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY from the environment" in err
    assert PROJECT_DOTENV not in err
    credential = claude_credential.resolve_claude_credential()
    assert credential.api_key == ENV_KEY
    _assert_no_secret(repr(credential))


def test_dotenv_key_is_used_last_and_names_its_file(
    monkeypatch, logged_out, project_key, capsys
):
    sentinel = _stub_sdk_client(monkeypatch)
    assert make_judge_client() is sentinel
    err = capsys.readouterr().err
    assert f"Claude judge: using ANTHROPIC_API_KEY from {PROJECT_DOTENV}" in err
    _assert_no_secret(err)


def test_install_tree_dotenv_key_is_labelled_with_its_file(
    monkeypatch, tmp_path, logged_out
):
    """load_env merges GAIA's own .env into os.environ; it is still a .env."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"ANTHROPIC_API_KEY={DOTENV_KEY}\n", encoding="utf-8")
    monkeypatch.setattr(gaia_env, "_install_tree_dotenv", lambda: dotenv)
    monkeypatch.setenv("ANTHROPIC_API_KEY", DOTENV_KEY)
    credential = claude_credential.resolve_claude_credential()
    assert credential.label == f"ANTHROPIC_API_KEY from {dotenv}"


def test_no_credential_raises_actionable_error(no_claude_bin):
    with pytest.raises(ValueError) as excinfo:
        make_judge_client()
    message = str(excinfo.value)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in message
    assert "claude setup-token" in message
    assert "/login" in message
    assert "npm install -g @anthropic-ai/claude-code" in message
    # The three options, in the order they are tried.
    sub = message.index("SUBSCRIPTION")
    env_key = message.index("export ANTHROPIC_API_KEY")
    dotenv = message.index(".env file in the working")
    assert sub < env_key < dotenv


def test_logged_out_cli_and_no_key_raises(logged_out):
    with pytest.raises(ValueError, match="No Claude credential"):
        make_judge_client()


def test_blank_credentials_count_as_absent(monkeypatch, no_claude_bin):
    """The workflow blanks the key rather than unsetting it — `''` must not win."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    with pytest.raises(ValueError):
        make_judge_client()


def test_token_without_a_cli_uses_the_key_and_warns(
    monkeypatch, no_claude_bin, exported_key, caplog, capsys
):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    sentinel = _stub_sdk_client(monkeypatch)
    with caplog.at_level("WARNING", logger=claude_credential.log.name):
        assert make_judge_client() is sentinel
    assert "the `claude` CLI is not on PATH" in caplog.text
    _assert_no_secret(caplog.text + capsys.readouterr().err)


def test_missing_claude_cli_is_an_actionable_error(monkeypatch, no_claude_bin):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", OAUTH)
    with pytest.raises(RuntimeError) as excinfo:
        make_judge_client()
    assert "npm install -g @anthropic-ai/claude-code" in str(excinfo.value)
    _assert_no_secret(str(excinfo.value))


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, False),
        ({"ANTHROPIC_API_KEY": "  "}, False),
        ({"CLAUDE_CODE_OAUTH_TOKEN": ""}, False),
        ({"ANTHROPIC_API_KEY": ENV_KEY}, True),
    ],
)
def test_judge_credential_present(monkeypatch, no_claude_bin, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert judge_client.judge_credential_present() is expected


def test_a_logged_in_cli_counts_as_present(fake_claude_bin):
    assert judge_client.judge_credential_present() is True


def test_a_logged_out_cli_does_not_count_as_present(logged_out):
    assert judge_client.judge_credential_present() is False


def test_max_tokens_reaches_only_the_sdk_client(monkeypatch, no_claude_bin):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_KEY)
    sentinel = _stub_sdk_client(monkeypatch)
    make_judge_client(model="m", max_tokens=8192)
    assert sentinel.kwargs == {"model": "m", "max_tokens": 8192}
    make_judge_client(model="m")
    # Unset, the client's own default applies.
    assert sentinel.kwargs == {"model": "m"}


def test_the_message_is_printed_once(monkeypatch, fake_claude_bin, capsys):
    make_judge_client()
    make_judge_client()
    assert capsys.readouterr().err.count("Claude judge: using") == 1


# ---------------------------------------------------------------------------
# The CLI login check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,logged_in",
    [
        ({"loggedIn": True, "authMethod": "claude.ai"}, True),
        ({"loggedIn": True, "authMethod": "oauth_token"}, True),
        # A key /login stored is the CLI's own login.
        (
            {
                "loggedIn": True,
                "authMethod": "api_key",
                "apiKeySource": "/login managed key",
            },
            True,
        ),
        ({"loggedIn": False, "authMethod": "none"}, False),
        # A key from the env or a settings helper is not a login: the env var is
        # step 2, and the judge does not load settings.
        (
            {
                "loggedIn": True,
                "authMethod": "api_key",
                "apiKeySource": "ANTHROPIC_API_KEY",
            },
            False,
        ),
        (
            {"loggedIn": True, "authMethod": "api_key", "apiKeySource": "apiKeyHelper"},
            False,
        ),
        ({}, False),
    ],
)
def test_cli_login_reading(monkeypatch, status, logged_in):
    monkeypatch.setattr(claude_credential, "_auth_status", lambda _b, _e: status)
    assert claude_credential.cli_logged_in("/usr/bin/claude", {}) is logged_in


def test_auth_status_runs_without_the_key_or_token(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 1, '{"loggedIn": false}', "")

    monkeypatch.setattr(claude_credential.subprocess, "run", fake_run)
    status = _REAL_AUTH_STATUS(
        "/usr/bin/claude",
        {"ANTHROPIC_API_KEY": ENV_KEY, "CLAUDE_CODE_OAUTH_TOKEN": OAUTH, "A": "1"},
    )
    assert status == {"loggedIn": False}
    assert captured["cmd"] == ["/usr/bin/claude", "auth", "status", "--json"]
    assert captured["env"] == {"A": "1"}


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.TimeoutExpired("claude", 30),
        FileNotFoundError("claude"),
        subprocess.CompletedProcess([], 1, "error: unknown command 'auth'", ""),
    ],
)
def test_an_unreadable_login_check_is_an_error(monkeypatch, outcome):
    def fake_run(cmd, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(claude_credential.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="npm install -g @anthropic-ai/claude-code"):
        _REAL_AUTH_STATUS("/usr/bin/claude", {})


def test_the_login_check_runs_once_per_cli(monkeypatch):
    calls = []

    def status(_bin, _env):
        calls.append(_bin)
        return {"loggedIn": True}

    monkeypatch.setattr(claude_credential, "_auth_status", status)
    for _ in range(3):
        claude_credential.cli_logged_in("/usr/bin/claude", {})
    assert calls == ["/usr/bin/claude"]


def test_the_cli_judge_child_does_not_inherit_the_key(
    monkeypatch, fake_claude_bin, exported_key
):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        payload = {"type": "result", "is_error": False, "result": "ok"}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    monkeypatch.setattr(judge_client.subprocess, "run", fake_run)
    make_judge_client().get_completion("judge this")
    assert "ANTHROPIC_API_KEY" not in captured["env"]


# ---------------------------------------------------------------------------
# ClaudeCliClient.get_completion
# ---------------------------------------------------------------------------


def _run_returning(monkeypatch, *, returncode=0, stdout="", stderr=""):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(judge_client.subprocess, "run", fake_run)
    return captured


def _run_replying(monkeypatch, result, *, is_error=False, api_error_status=None):
    """Stub a `--output-format json` reply the way the real CLI emits it."""
    return _run_returning(
        monkeypatch,
        stdout=json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": is_error,
                "api_error_status": api_error_status,
                "result": result,
            }
        ),
    )


def test_get_completion_returns_sdk_shaped_blocks(monkeypatch, fake_claude_bin):
    _run_replying(monkeypatch, '  {"approved": true}\n')
    blocks = ClaudeCliClient(model="claude-opus-5").get_completion("judge this")
    # The three judge factories all do exactly this to read a verdict.
    text = "".join(getattr(b, "text", "") for b in blocks if hasattr(b, "text"))
    assert text == '{"approved": true}'


def test_get_completion_sends_the_prompt_over_stdin(monkeypatch, fake_claude_bin):
    captured = _run_replying(monkeypatch, "ok")
    prompt = "a very long judge prompt " * 2000
    ClaudeCliClient().get_completion(prompt)
    assert captured["kwargs"]["input"] == prompt
    assert prompt not in captured["cmd"]


def test_get_completion_isolates_the_judge_context(monkeypatch, fake_claude_bin):
    """No project settings, no CLAUDE.md, no MCP servers, no tools, no sessions."""
    captured = _run_replying(monkeypatch, "ok")
    ClaudeCliClient(model="claude-opus-5").get_completion("judge this")
    cmd = captured["cmd"]
    assert cmd[1] == "-p"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers": {}}'
    assert "--no-session-persistence" in cmd
    assert "Bash" in cmd[cmd.index("--disallowed-tools") + 1].split(",")
    assert "Read" in cmd[cmd.index("--disallowed-tools") + 1].split(",")
    # Claude Code's agent prompt is replaced, not appended to.
    assert "evaluation judge" in cmd[cmd.index("--system-prompt") + 1]
    # A temp cwd is what keeps the repo's own CLAUDE.md out of the judge prompt.
    assert captured["kwargs"]["cwd"]


def test_rejected_credential_surfaces_the_cli_output(monkeypatch, fake_claude_bin):
    _run_returning(
        monkeypatch,
        returncode=1,
        stderr="Invalid API key · Please run /login",
    )
    with pytest.raises(RuntimeError) as excinfo:
        ClaudeCliClient().get_completion("judge this")
    message = str(excinfo.value)
    assert "exited 1" in message
    assert "Invalid API key" in message
    assert "claude setup-token" in message


def test_api_error_on_a_zero_exit_is_not_returned_as_a_verdict(
    monkeypatch, fake_claude_bin
):
    """The CLI exits 0 and prints API errors as the result — is_error is the tell.

    Without this check the judge would score every case with the string
    "API Error: 400 ...", which records as an unusable verdict rather than a
    transport failure and lets a wholly unjudged run report green.
    """
    _run_replying(
        monkeypatch,
        "API Error: 400 Your credit balance is too low",
        is_error=True,
        api_error_status=400,
    )
    with pytest.raises(RuntimeError) as excinfo:
        ClaudeCliClient().get_completion("judge this")
    message = str(excinfo.value)
    assert "is_error=true" in message
    assert "credit balance is too low" in message


def test_non_json_stdout_raises(monkeypatch, fake_claude_bin):
    _run_returning(monkeypatch, stdout="not json at all")
    with pytest.raises(RuntimeError) as excinfo:
        ClaudeCliClient().get_completion("judge this")
    assert "not json at all" in str(excinfo.value)


def test_empty_reply_raises_rather_than_scoring_nothing(monkeypatch, fake_claude_bin):
    _run_replying(monkeypatch, "   \n")
    with pytest.raises(RuntimeError) as excinfo:
        ClaudeCliClient().get_completion("judge this")
    assert "empty result" in str(excinfo.value)


def test_timeout_raises_with_the_configured_budget(monkeypatch, fake_claude_bin):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 42)

    monkeypatch.setattr(judge_client.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as excinfo:
        ClaudeCliClient(timeout=42).get_completion("judge this")
    assert "timed out after 42s" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Preflight probe
# ---------------------------------------------------------------------------


def test_probe_returns_the_reply_when_the_credential_is_accepted(
    monkeypatch, fake_claude_bin
):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    _run_replying(monkeypatch, "ok")
    assert probe_judge_credential() == "ok"


def test_probe_fails_when_the_credential_is_rejected(monkeypatch, fake_claude_bin):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-expired")
    _run_replying(
        monkeypatch,
        "API Error: 400 credit balance is too low",
        is_error=True,
        api_error_status=400,
    )
    with pytest.raises(RuntimeError) as excinfo:
        probe_judge_credential()
    assert "credit balance is too low" in str(excinfo.value)


def test_probe_cli_exits_nonzero_on_a_missing_credential(capsys, no_claude_bin):
    assert judge_client.main(["--probe"]) == 1
    assert "Judge credential probe FAILED" in capsys.readouterr().err


def test_probe_cli_exits_zero_when_accepted(monkeypatch, fake_claude_bin, capsys):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    _run_replying(monkeypatch, "ok")
    assert judge_client.main(["--probe"]) == 0
    assert "accepted by a live probe" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ClaudeCliClient.get_completion_with_usage (corpus generation)
# ---------------------------------------------------------------------------


def _run_with_usage(monkeypatch, usage, cost=0.03, result="generated text"):
    payload = {
        "type": "result",
        "is_error": False,
        "result": result,
        "num_turns": 1,
        "duration_api_ms": 1200,
        "total_cost_usd": cost,
    }
    if usage is not None:
        payload["usage"] = usage
    return _run_returning(monkeypatch, stdout=json.dumps(payload))


def test_usage_counts_cached_input_and_keeps_the_reported_cost(
    monkeypatch, fake_claude_bin
):
    _run_with_usage(
        monkeypatch,
        {
            "input_tokens": 10,
            "cache_creation_input_tokens": 2000,
            "cache_read_input_tokens": 500,
            "output_tokens": 300,
        },
        cost=0.0425,
    )
    out = ClaudeCliClient(model="claude-sonnet-5").get_completion_with_usage("go")
    assert [b.text for b in out["content"]] == ["generated text"]
    assert out["usage"] == {
        "input_tokens": 2510,
        "output_tokens": 300,
        "total_tokens": 2810,
    }
    cost = out["cost"]
    assert cost["total_cost"] == 0.0425
    assert cost["input_cost"] + cost["output_cost"] == pytest.approx(0.0425)
    assert cost["input_cost"] > 0 and cost["output_cost"] > 0


def test_usage_with_zero_tokens_does_not_divide_by_zero(monkeypatch, fake_claude_bin):
    _run_with_usage(monkeypatch, {"input_tokens": 0, "output_tokens": 0}, cost=0)
    out = ClaudeCliClient().get_completion_with_usage("go")
    assert out["cost"] == {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}


def test_unknown_model_prices_with_the_default_row(monkeypatch, fake_claude_bin):
    _run_with_usage(monkeypatch, {"input_tokens": 100, "output_tokens": 100}, cost=1)
    out = ClaudeCliClient(model="not-a-real-model").get_completion_with_usage("go")
    assert out["cost"]["total_cost"] == 1


def test_missing_usage_block_is_an_error_not_zero(monkeypatch, fake_claude_bin):
    _run_with_usage(monkeypatch, None)
    with pytest.raises(RuntimeError, match="no `usage` block"):
        ClaudeCliClient().get_completion_with_usage("go")


def test_usage_call_still_rejects_an_api_error(monkeypatch, fake_claude_bin):
    _run_replying(monkeypatch, "API Error: 401", is_error=True, api_error_status=401)
    with pytest.raises(RuntimeError, match="is_error=true"):
        ClaudeCliClient().get_completion_with_usage("go")


def test_failure_preamble_names_the_login(monkeypatch, fake_claude_bin):
    _run_returning(monkeypatch, returncode=1, stderr="Not logged in")
    with pytest.raises(RuntimeError, match="/login"):
        ClaudeCliClient().get_completion("judge this")
