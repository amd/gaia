# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Judge transport selection and the `claude -p` judge client.

Covers the two things a broken judge credential used to get wrong silently:
which transport gets picked for which credential, and whether a rejected or
empty CLI reply surfaces as an actionable error instead of an unscored case.
"""

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import gaia
from gaia.eval import judge_client
from gaia.eval.judge_client import (
    ClaudeCliClient,
    make_judge_client,
    probe_judge_credential,
)


@pytest.fixture(autouse=True)
def _clear_credentials(monkeypatch):
    """Neither credential is set unless a test sets it.

    `load_dotenv()` at import time can populate either from a developer's local
    `.env`, which would otherwise make selection tests pass for the wrong reason.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delitem(gaia._PRE_DOTENV_ENVIRON, "ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)


@pytest.fixture
def fake_claude_bin(monkeypatch):
    monkeypatch.setattr(judge_client.shutil, "which", lambda _: "/usr/bin/claude")


@pytest.fixture
def no_claude_bin(monkeypatch):
    monkeypatch.setattr(judge_client.shutil, "which", lambda _: None)


# ---------------------------------------------------------------------------
# Transport selection
# ---------------------------------------------------------------------------


def test_no_credential_raises_actionable_error(no_claude_bin):
    with pytest.raises(ValueError) as excinfo:
        make_judge_client()
    message = str(excinfo.value)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in message
    assert "ANTHROPIC_API_KEY" in message
    assert "claude setup-token" in message
    assert "/login" in message
    assert "npm install -g @anthropic-ai/claude-code" in message


def test_logged_in_cli_needs_no_variable(fake_claude_bin):
    """#1344: like the runner, a CLI login is a subscription credential."""
    client = make_judge_client(model="claude-sonnet-5")
    assert isinstance(client, ClaudeCliClient)
    assert client.model == "claude-sonnet-5"


def test_blank_credentials_count_as_absent(monkeypatch, no_claude_bin):
    """The workflow blanks the key rather than unsetting it — `''` must not win."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    with pytest.raises(ValueError):
        make_judge_client()


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, False),
        ({"ANTHROPIC_API_KEY": "  "}, False),
        ({"CLAUDE_CODE_OAUTH_TOKEN": ""}, False),
        ({"ANTHROPIC_API_KEY": "sk-ant-api-test"}, True),
        ({"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-test"}, True),
    ],
)
def test_judge_credential_present(monkeypatch, no_claude_bin, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
        if key == "ANTHROPIC_API_KEY" and value.strip():
            monkeypatch.setitem(gaia._PRE_DOTENV_ENVIRON, key, value)
    assert judge_client.judge_credential_present() is expected


def test_a_claude_cli_on_path_counts_as_present(fake_claude_bin):
    assert judge_client.judge_credential_present() is True


def test_oauth_token_selects_the_cli_transport(monkeypatch, fake_claude_bin):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    client = make_judge_client(model="claude-sonnet-5")
    assert isinstance(client, ClaudeCliClient)
    assert client.model == "claude-sonnet-5"


def _set_api_key(monkeypatch, value):
    monkeypatch.setenv("ANTHROPIC_API_KEY", value)
    monkeypatch.setitem(gaia._PRE_DOTENV_ENVIRON, "ANTHROPIC_API_KEY", value)


def _stub_sdk_client(monkeypatch):
    """Stand in for gaia.eval.claude so no real Anthropic client is built."""
    sentinel = SimpleNamespace(kwargs=None)

    def build(**kwargs):
        sentinel.kwargs = kwargs
        return sentinel

    monkeypatch.setitem(
        sys.modules, "gaia.eval.claude", SimpleNamespace(ClaudeClient=build)
    )
    return sentinel


def test_api_key_selects_the_sdk_transport(monkeypatch):
    _set_api_key(monkeypatch, "sk-ant-api-test")
    sentinel = _stub_sdk_client(monkeypatch)
    assert make_judge_client(model="claude-opus-5") is sentinel


def test_api_key_wins_when_both_are_set(monkeypatch, fake_claude_bin):
    _set_api_key(monkeypatch, "sk-ant-api-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    sentinel = _stub_sdk_client(monkeypatch)
    assert make_judge_client() is sentinel


def test_max_tokens_reaches_only_the_sdk_client(monkeypatch):
    _set_api_key(monkeypatch, "sk-ant-api-test")
    sentinel = _stub_sdk_client(monkeypatch)
    make_judge_client(model="m", max_tokens=8192)
    assert sentinel.kwargs == {"model": "m", "max_tokens": 8192}
    make_judge_client(model="m")
    # Unset, the client's own default applies.
    assert sentinel.kwargs == {"model": "m"}


def test_missing_claude_cli_is_an_actionable_error(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    monkeypatch.setattr(judge_client.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError) as excinfo:
        make_judge_client()
    assert "npm install -g @anthropic-ai/claude-code" in str(excinfo.value)


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
