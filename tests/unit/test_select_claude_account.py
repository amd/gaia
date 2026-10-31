# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CI picks its Claude account per job: primary, else the backup on quota, else fail.

The repo variable this replaced needed a PAT nobody set to switch back, so CI sat
on an exhausted secondary while the primary had quota. The rule pinned here:
only a quota refusal moves a job to the other account; a rejected credential or
an unreadable reply fails at once and names the secret to fix.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "util" / "select_claude_account.py"


@pytest.fixture(scope="module")
def sel():
    spec = importlib.util.spec_from_file_location("select_claude_account", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _prober(sel, **answers):
    calls = []

    def probe(account):
        calls.append(account)
        return answers[account]

    probe.calls = calls
    return probe


def ok(sel):
    return sel.Probe("ok", "ok")


def quota(sel, resets):
    return sel.Probe("quota", f"You've hit your weekly limit, resets {resets}", resets)


BOTH = {"primary": True, "secondary": True}


def test_primary_answers(sel):
    probe = _prober(sel, primary=ok(sel))
    result = sel.select(BOTH, probe, what="Tonight's audit")
    assert (result.account, result.exit_code, result.verdict) == ("primary", 0, "ok")
    # The backup is not spent on a probe when the primary answers.
    assert probe.calls == ["primary"]
    assert result.commands == [
        "::notice title=Claude account::Tonight's audit uses the primary Claude "
        "account (CLAUDE_CODE_OAUTH_TOKEN)."
    ]


def test_primary_out_of_quota_uses_the_secondary(sel):
    probe = _prober(sel, primary=quota(sel, "3pm (UTC)"), secondary=ok(sel))
    result = sel.select(BOTH, probe)
    assert (result.account, result.exit_code) == ("secondary", 0)
    assert probe.calls == ["primary", "secondary"]
    warning = result.commands[0]
    assert warning.startswith("::warning title=Claude account: using the backup::")
    assert "3pm (UTC)" in warning
    assert "secondary" in result.summary and "3pm (UTC)" in result.summary


def test_both_out_of_quota_fails_naming_both_resets(sel):
    probe = _prober(
        sel, primary=quota(sel, "3pm (UTC)"), secondary=quota(sel, "Oct 31, 9am")
    )
    result = sel.select(BOTH, probe, quota_note="Tomorrow's run covers tonight.")
    assert (result.account, result.verdict) == ("", "quota")
    assert result.exit_code == sel.EXIT_QUOTA
    error = result.commands[0]
    assert error.startswith("::error title=Claude unavailable::")
    assert "both Claude accounts are out of quota" in error
    assert "3pm (UTC)" in error and "Oct 31, 9am" in error
    assert "Tomorrow's run covers tonight." in result.summary


@pytest.mark.parametrize("kind", ["credential", "unknown"])
def test_a_bad_primary_never_falls_back(sel, kind):
    probe = _prober(sel, primary=sel.Probe(kind, "Invalid API key"))
    result = sel.select(BOTH, probe)
    assert result.account == ""
    assert result.exit_code in (sel.EXIT_CREDENTIAL, sel.EXIT_UNKNOWN)
    # The secondary is never asked: switching would hide a token to rotate.
    assert probe.calls == ["primary"]
    if kind == "credential":
        assert "gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo" in result.commands[0]


def test_a_rejected_secondary_names_the_secondary_secret(sel):
    probe = _prober(
        sel,
        primary=quota(sel, "3pm"),
        secondary=sel.Probe("credential", "OAuth token has expired"),
    )
    result = sel.select(BOTH, probe)
    assert result.exit_code == sel.EXIT_CREDENTIAL
    assert "gh secret set CLAUDE_CODE_OAUTH_TOKEN_SECONDARY" in result.commands[0]


def test_primary_out_of_quota_with_no_secondary_says_so(sel):
    probe = _prober(sel, primary=quota(sel, "3pm (UTC)"))
    result = sel.select({"primary": True, "secondary": False}, probe)
    assert result.exit_code == sel.EXIT_QUOTA
    assert probe.calls == ["primary"]
    error = result.commands[0]
    assert "CLAUDE_CODE_OAUTH_TOKEN_SECONDARY is not set" in error
    assert "3pm (UTC)" in error


def test_only_a_secondary_is_used_with_a_warning(sel):
    probe = _prober(sel, secondary=ok(sel))
    result = sel.select({"primary": False, "secondary": True}, probe)
    assert result.account == "secondary"
    assert "CLAUDE_CODE_OAUTH_TOKEN is not set" in result.commands[0]


def test_no_token_at_all_leaves_the_api_key_path(sel):
    probe = _prober(sel)
    result = sel.select({"primary": False, "secondary": False}, probe)
    assert (result.account, result.exit_code, probe.calls) == ("none", 0, [])
    assert "ANTHROPIC_API_KEY" in result.commands[0]


# --- reading the CLI's reply ----------------------------------------------------


@pytest.mark.parametrize(
    "stdout, stderr, code, kind, resets",
    [
        (
            json.dumps({"type": "result", "is_error": False, "result": "ok"}),
            "",
            0,
            "ok",
            None,
        ),
        (
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "result": "You've hit your weekly limit · resets 3pm (UTC)",
                }
            ),
            "",
            1,
            "quota",
            "3pm (UTC)",
        ),
        # A refusal delivered as an ordinary completion is still a refusal.
        (
            json.dumps(
                {"type": "result", "is_error": False, "result": "Usage limit reached"}
            ),
            "",
            0,
            "quota",
            None,
        ),
        (
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "result": "Invalid API key · Please run /login",
                }
            ),
            "",
            1,
            "credential",
            None,
        ),
        # The bare subscription refusal: no "weekly", no "usage".
        (
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "result": "You've hit your limit · resets 3pm (UTC)",
                }
            ),
            "",
            1,
            "quota",
            "3pm (UTC)",
        ),
        # A per-minute throttle is not an empty account: never a failover.
        (
            json.dumps(
                {
                    "type": "result",
                    "is_error": True,
                    "result": "API Error: 429 rate_limit_error",
                }
            ),
            "",
            1,
            "unknown",
            None,
        ),
        ("", "Error: something unexpected", 1, "unknown", None),
        # Answered, but not with the word asked for.
        (
            json.dumps({"type": "result", "is_error": False, "result": "hi"}),
            "",
            0,
            "unknown",
            None,
        ),
    ],
    ids=[
        "ok",
        "weekly-limit",
        "limit-as-completion",
        "credential",
        "bare-limit",
        "per-minute-429",
        "crash",
        "no-ok",
    ],
)
def test_classify_reply(sel, stdout, stderr, code, kind, resets):
    probe = sel.classify_reply(stdout, stderr, code)
    assert probe.kind == kind
    assert probe.resets == resets


def test_epoch_reset_is_rendered_as_a_time(sel):
    assert sel.reset_time("Claude AI usage limit reached|1761062400") == (
        "2025-10-21 16:00 UTC"
    )


# --- main: masking and outputs ------------------------------------------------


def test_main_masks_both_tokens_and_writes_only_labels(
    sel, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("CLAUDE_TOKEN_PRIMARY", "primary-secret-value")
    monkeypatch.setenv("CLAUDE_TOKEN_SECONDARY", "secondary-secret-value")
    monkeypatch.setattr(sel.shutil, "which", lambda _name: "/fake/claude")
    seen = []

    def fake_probe(token, model, claude_bin, timeout):
        seen.append((token, model, claude_bin))
        if token == "primary-secret-value":
            return sel.Probe("quota", "weekly limit, resets 3pm", "3pm")
        return sel.Probe("ok", "ok")

    monkeypatch.setattr(sel, "run_probe", fake_probe)
    output = tmp_path / "out"
    summary = tmp_path / "summary"
    code = sel.main(
        [
            "--model",
            "claude-opus-5-5",
            "--github-output",
            str(output),
            "--summary",
            str(summary),
        ]
    )
    assert code == 0
    assert [s[0] for s in seen] == ["primary-secret-value", "secondary-secret-value"]
    assert output.read_text(encoding="utf-8") == "account=secondary\nverdict=ok\n"
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == "::add-mask::primary-secret-value"
    assert lines[1] == "::add-mask::secondary-secret-value"
    # Past the mask commands, no token appears anywhere.
    rest = "\n".join(lines[2:]) + summary.read_text(encoding="utf-8")
    assert "secret-value" not in rest


def test_main_fails_loudly_without_the_cli(sel, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_TOKEN_PRIMARY", "t")
    monkeypatch.delenv("CLAUDE_TOKEN_SECONDARY", raising=False)
    monkeypatch.setattr(sel.shutil, "which", lambda _name: None)
    assert sel.main([]) == sel.EXIT_UNKNOWN
    assert "::error title=Claude CLI missing::" in capsys.readouterr().out


def test_the_probe_child_never_sees_an_api_key_or_the_other_token(sel, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-key")
    monkeypatch.setenv("CLAUDE_TOKEN_SECONDARY", "other")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs["env"])
        captured["cmd"] = cmd

        class Done:
            returncode = 0
            stdout = json.dumps({"type": "result", "is_error": False, "result": "ok"})
            stderr = ""

        return Done()

    monkeypatch.setattr(sel.subprocess, "run", fake_run)
    probe = sel.run_probe("this-token", "claude-opus-5-5", "claude", 30)
    assert probe.kind == "ok"
    assert captured["CLAUDE_CODE_OAUTH_TOKEN"] == "this-token"
    assert "ANTHROPIC_API_KEY" not in captured
    assert "CLAUDE_TOKEN_SECONDARY" not in captured
    # The token travels by env, never argv.
    assert "this-token" not in captured["cmd"]
