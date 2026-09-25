# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for util/classify_claude_probe.py and the workflows that call it.

The fixtures here carry the verbatim rejection text from run 35654783773,
including its U+00B7 separators. That is deliberate: this test file may be
non-ASCII, while the util module and the PowerShell workflow steps may not be
(PS 5.1 reads a BOM-less file as ANSI, so one non-ASCII byte kills the step) -
and the last test in this file is what enforces that.
"""

import json
import sys
from pathlib import Path

import pytest

# Ensure util/ is importable regardless of where pytest is invoked from.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import classify_claude_probe as ccp  # noqa: E402

# Verbatim from the failing "Preflight" step of run 35654783773 (#4080).
REAL_QUOTA_REJECTION = (
    "You've hit your org's monthly spend limit · ask your admin to raise it at "
    "claude.ai/admin-settings/usage · your weekly limit resets Sep 23, 8am "
    "(America/Los_Angeles)"
)

# Verbatim from the failing "Scenario eval" preflight of run 35681533582,
# job 106629918099. The BOM is real: the step writes the probe with
# PowerShell's `Set-Content -Encoding UTF8`, and the classifier reads that
# file back as utf-8 rather than utf-8-sig, so U+FEFF reaches classify().
UTF8_BOM = chr(0xFEFF)
REAL_WEEKLY_LIMIT_REJECTION = (
    UTF8_BOM + "You've hit your weekly limit · resets Sep 23, 8am "
    "(America/Los_Angeles)"
)

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
EVAL_WORKFLOW = WORKFLOWS / "eval_flagship.yml"
CANARY_WORKFLOW = WORKFLOWS / "claude-auth-canary.yml"


def _stream_json_log(assistant_text: str, is_error: bool = False) -> str:
    """A claude-code-action execution_file shaped like the real thing."""
    return json.dumps(
        [
            {"type": "system", "subtype": "init", "session_id": "abc123"},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Reply with exactly the word: ok"}
                    ],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": assistant_text}],
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "result": assistant_text,
                "is_error": is_error,
            },
        ]
    )


# ---------------------------------------------------------------------------
# classify() - the discriminator the issue asks for
# ---------------------------------------------------------------------------


def test_real_spend_limit_rejection_is_a_quota_problem():
    v = ccp.classify(REAL_QUOTA_REJECTION, exit_code=1)
    assert v.kind == "quota"
    assert v.exit_code == ccp.EXIT_QUOTA
    assert "QUOTA EXHAUSTED" in v.summary
    assert "spend limit" in v.matched
    # The whole point: it must send the reader to billing, not to the secrets.
    assert "Do NOT rotate the repository secrets" in v.action
    assert "admin-settings/usage" in v.action


def test_real_weekly_limit_rejection_is_a_quota_problem():
    """The phrase that turned the spend cap into five fake eval regressions.

    It names no spend limit and carries a U+00B7 between "limit" and "resets",
    so neither "spend limit" nor "limit resets" saw it.
    """
    v = ccp.classify(REAL_WEEKLY_LIMIT_REJECTION, exit_code=1)
    assert v.kind == "quota"
    assert v.exit_code == ccp.EXIT_QUOTA
    assert "weekly limit" in v.matched
    assert "Do NOT rotate the repository secrets" in v.action


def test_the_weekly_limit_pattern_does_not_depend_on_the_reset_date():
    """The date moves every reset; matching on it would re-break next week."""
    v = ccp.classify("You've hit your weekly limit - resets Jan 5, 3pm", exit_code=1)
    assert v.kind == "quota"


def test_main_reports_the_weekly_limit_as_quota(tmp_path, capsys):
    """End to end through the eval preflight's own invocation."""
    p = tmp_path / "claude_probe.txt"
    p.write_text(REAL_WEEKLY_LIMIT_REJECTION, encoding="utf-8")
    assert ccp.main(["--exit-code", "1", "--text-file", str(p)]) == ccp.EXIT_QUOTA
    assert "QUOTA EXHAUSTED" in capsys.readouterr().out


def test_401_body_is_a_credential_problem():
    v = ccp.classify(
        "API Error: 401 authentication_error: invalid x-api-key", exit_code=1
    )
    assert v.kind == "credential"
    assert v.exit_code == ccp.EXIT_CREDENTIAL
    assert "CREDENTIAL INVALID" in v.summary
    assert "setup-token" in v.action
    assert "Do NOT ask for a spend-limit increase" in v.action


def test_429_rate_limit_is_a_quota_problem():
    v = ccp.classify("API Error: 429 rate_limit_error", exit_code=1)
    assert v.kind == "quota"
    assert v.exit_code == ccp.EXIT_QUOTA


def test_regression_guard_a_working_credential_can_never_be_failed():
    """A zero-exit probe is never scanned, so no pattern can fail a good key."""
    v = ccp.classify("ok (you are well under your rate limit and quota)", exit_code=0)
    assert v.kind == "ok"
    assert v.exit_code == ccp.EXIT_OK


def test_always_scan_catches_a_quota_rejection_delivered_as_success():
    v = ccp.classify(REAL_QUOTA_REJECTION, exit_code=0, always_scan=True)
    assert v.kind == "quota"
    assert v.exit_code == ccp.EXIT_QUOTA


def test_always_scan_still_passes_a_real_reply():
    v = ccp.classify("ok", exit_code=0, always_scan=True)
    assert v.kind == "ok"
    assert v.exit_code == ccp.EXIT_OK


def test_unrecognised_rejection_fails_loudly_instead_of_guessing():
    v = ccp.classify("something else entirely", exit_code=1)
    assert v.kind == "unclassified"
    assert v.exit_code == ccp.EXIT_UNCLASSIFIED
    assert "QUOTA_PATTERNS or CREDENTIAL_PATTERNS" in v.action


def test_a_body_matching_both_classes_is_ambiguous_not_a_coin_flip():
    v = ccp.classify(
        "authentication_error: your org has hit its monthly spend limit", exit_code=1
    )
    assert v.kind == "unclassified"
    assert "ambiguous" in v.summary
    assert "spend limit" in v.matched
    assert "authentication_error" in v.matched


def test_a_status_code_inside_a_longer_number_is_not_a_quota_signal():
    v = ccp.classify("the run used 402913 tokens and then stopped", exit_code=1)
    assert v.kind == "unclassified"


# ---------------------------------------------------------------------------
# extract_reply() - only the model's own words
# ---------------------------------------------------------------------------


def test_extract_reply_returns_the_model_words_not_the_prompt():
    reply = ccp.extract_reply(_stream_json_log("ok"), "stream-json")
    assert "ok" in reply
    # The log echoes the prompt; matching on it would let the canary pass on
    # its own question.
    assert "Reply with exactly" not in reply


def test_extract_reply_feeds_a_quota_rejection_straight_into_classify():
    reply = ccp.extract_reply(_stream_json_log(REAL_QUOTA_REJECTION), "stream-json")
    v = ccp.classify(reply, exit_code=0, always_scan=True)
    assert v.kind == "quota"


def test_extract_reply_text_format_is_passed_through():
    assert ccp.extract_reply("raw output", "text") == "raw output"


def test_extract_reply_rejects_an_empty_log():
    with pytest.raises(ValueError, match="empty"):
        ccp.extract_reply("   ", "stream-json")


def test_extract_reply_rejects_unparseable_garbage():
    with pytest.raises(ValueError, match="could not parse"):
        ccp.extract_reply("not json at all {{{", "stream-json")


# ---------------------------------------------------------------------------
# main() - what the workflow steps actually run
# ---------------------------------------------------------------------------


def test_main_reports_quota_with_its_own_exit_code(tmp_path, capsys):
    p = tmp_path / "probe.txt"
    p.write_text(REAL_QUOTA_REJECTION, encoding="utf-8")
    rc = ccp.main(["--exit-code", "1", "--text-file", str(p)])
    assert rc == ccp.EXIT_QUOTA
    out = capsys.readouterr().out
    assert out.startswith("::error::")
    assert "QUOTA EXHAUSTED" in out


def test_main_passes_a_healthy_probe(tmp_path, capsys):
    p = tmp_path / "probe.txt"
    p.write_text("ok", encoding="utf-8")
    assert ccp.main(["--exit-code", "0", "--text-file", str(p)]) == ccp.EXIT_OK
    assert "accepted" in capsys.readouterr().out


def test_main_missing_file_fails_rather_than_passing(tmp_path, capsys):
    missing = tmp_path / "nope.txt"
    rc = ccp.main(["--exit-code", "0", "--text-file", str(missing)])
    assert rc == ccp.EXIT_UNCLASSIFIED
    assert "::error::" in capsys.readouterr().out


def test_main_canary_mode_names_quota_rather_than_a_missing_reply(tmp_path, capsys):
    """Classification beats the --expect check: the reader needs the cause."""
    p = tmp_path / "log.json"
    p.write_text(_stream_json_log(REAL_QUOTA_REJECTION), encoding="utf-8")
    rc = ccp.main(
        [
            "--exit-code",
            "0",
            "--text-file",
            str(p),
            "--format",
            "stream-json",
            "--always-scan",
            "--expect",
            "ok",
        ]
    )
    assert rc == ccp.EXIT_QUOTA
    assert "QUOTA EXHAUSTED" in capsys.readouterr().out


def test_main_canary_mode_fails_when_the_model_never_said_ok(tmp_path, capsys):
    p = tmp_path / "log.json"
    p.write_text(_stream_json_log("I cannot help with that request."), encoding="utf-8")
    rc = ccp.main(
        [
            "--exit-code",
            "0",
            "--text-file",
            str(p),
            "--format",
            "stream-json",
            "--always-scan",
            "--expect",
            "ok",
        ]
    )
    assert rc == ccp.EXIT_MISSING_REPLY
    assert "never replied" in capsys.readouterr().out


def test_main_canary_mode_passes_on_a_real_ok(tmp_path):
    p = tmp_path / "log.json"
    p.write_text(_stream_json_log("ok"), encoding="utf-8")
    rc = ccp.main(
        [
            "--exit-code",
            "0",
            "--text-file",
            str(p),
            "--format",
            "stream-json",
            "--always-scan",
            "--expect",
            "ok",
        ]
    )
    assert rc == ccp.EXIT_OK


# ---------------------------------------------------------------------------
# Workflow contract - the guards have to actually be wired up
# ---------------------------------------------------------------------------


def test_eval_preflight_classifies_the_probe():
    text = EVAL_WORKFLOW.read_text(encoding="utf-8")
    assert "util/classify_claude_probe.py --exit-code $probeExit" in text
    # The old undiscriminating error line must be gone, not merely supplemented.
    assert "a one-line probe was rejected" not in text


def test_canary_asserts_the_response_body():
    text = CANARY_WORKFLOW.read_text(encoding="utf-8")
    assert "util/classify_claude_probe.py" in text
    assert "--always-scan" in text
    assert "--expect ok" in text
    assert "--format stream-json" in text


def test_canary_checks_out_the_repo_before_the_classifier_runs():
    text = CANARY_WORKFLOW.read_text(encoding="utf-8")
    assert "actions/checkout" in text
    assert text.index("actions/checkout") < text.index(
        "python3 util/classify_claude_probe.py"
    )


def test_canary_notification_names_both_causes():
    text = CANARY_WORKFLOW.read_text(encoding="utf-8")
    assert "QUOTA EXHAUSTED" in text and "CREDENTIAL INVALID" in text
    # It must not pre-judge the cause as an expired token any more.
    assert "token may have expired" not in text


def _run_block_lines(raw: bytes) -> list:
    """Numbered lines of every literal `run: |` body in a workflow file.

    Only a block scalar counts. A bare `run:` mapping key (as under `defaults:`)
    introduces settings, not shell text, and the YAML comments around it never
    reach the generated script file.
    """
    out = []
    indent = None
    for number, line in enumerate(raw.split(b"\n"), start=1):
        stripped = line.strip()
        if indent is not None:
            if stripped and (len(line) - len(line.lstrip())) <= indent:
                indent = None
            else:
                out.append((number, line))
                continue
        if stripped in (b"run: |", b"run: |-", b"run: >", b"run: >-"):
            indent = len(line) - len(line.lstrip())
    return out


def test_eval_workflow_run_bodies_are_pure_ascii():
    """PS 5.1 reads a BOM-less file as ANSI - one non-ASCII byte kills the step.

    The workflow's own `defaults:` comment records the case (run 32274575191: a
    single em dash in an ::error:: message produced "Missing argument in
    parameter list" 30 lines away from itself). Asserted on bytes, because that
    is the only level at which a stray UTF-8 sequence is visible. Step `name:`
    values and YAML comments outside a run body are exempt - they never reach
    the script file.
    """
    raw = EVAL_WORKFLOW.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the workflow has a UTF-8 BOM"
    offenders = [
        (number, line.decode("utf-8", "replace"))
        for number, line in _run_block_lines(raw)
        if any(b > 127 for b in line)
    ]
    assert not offenders, f"non-ASCII inside a PowerShell run body: {offenders[:5]}"


def test_canary_probe_assertion_step_is_pure_ascii():
    """The classifier invocation must survive any shell, on any runner."""
    raw = CANARY_WORKFLOW.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "the workflow has a UTF-8 BOM"
    body = [
        line for _, line in _run_block_lines(raw) if b"classify_claude_probe" in line
    ]
    assert body, "the canary no longer invokes the classifier"
    for line in body:
        assert all(b < 128 for b in line)


def test_the_classifier_module_is_pure_ascii():
    src = (REPO_ROOT / "util" / "classify_claude_probe.py").read_text(encoding="utf-8")
    assert src.isascii()
