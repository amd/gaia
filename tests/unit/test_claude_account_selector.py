# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every Claude job picks its account at run time; nothing reads a repo variable.

CI used to read the account from ``vars.CLAUDE_ACCOUNT``. Switching it back needed a
PAT nobody set, so CI sat on an exhausted secondary while the primary had quota.
Each job now probes (``.github/actions/claude-credential``) or fails over inline
(``claude-run.yml``), and these tests keep it that way:

* no workflow reads ``vars.CLAUDE_ACCOUNT`` or dispatches a switch;
* a token is chosen by the label the probe reported, and that label comes from a
  probe in the same job or a ``needs`` job that exports it;
* the probe's outputs are labels, its tokens travel by env, and both tokens are
  scanned out of every public artifact;
* the backup token never reaches the agent under test.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
ACTION = REPO / ".github" / "actions" / "claude-credential" / "action.yml"

#: Fail over inline or probe both accounts on purpose, so they read each secret by name.
INLINE = {"claude-run.yml", "claude-auth-canary.yml"}

_LABEL = r"(?:steps\.claude_account\.outputs\.account|needs\.[\w-]+\.outputs\.claude_account)"
_PRIMARY = re.compile(r"secrets\.CLAUDE_CODE_OAUTH_TOKEN(?!_)")

#: Every way a non-inline workflow may read the primary token.
ALLOWED = [
    # The label picks the token.
    re.compile(
        rf"{_LABEL} == 'secondary' && secrets\.CLAUDE_CODE_OAUTH_TOKEN_SECONDARY"
        r" \|\| secrets\.CLAUDE_CODE_OAUTH_TOKEN \}\}"
    ),
    # The eval's backup: whichever account the job did not start on.
    re.compile(
        rf"CLAUDE_CODE_OAUTH_TOKEN_BACKUP: \$\{{\{{ {_LABEL} == 'secondary' && "
        r"secrets\.CLAUDE_CODE_OAUTH_TOKEN \|\| "
    ),
    # Input to the probe.
    re.compile(r"^\s*primary-token: \$\{\{ secrets\.CLAUDE_CODE_OAUTH_TOKEN \}\}$"),
    # Scans and presence checks read both secrets by name.
    re.compile(
        r"^\s*CLAUDE_CODE_OAUTH_TOKEN: \$\{\{ secrets\.CLAUDE_CODE_OAUTH_TOKEN \}\}$"
    ),
]


def _workflows():
    return sorted(WORKFLOWS.glob("*.yml"))


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _unexplained_reads(text: str) -> list[int]:
    return [
        number
        for number, line in enumerate(text.splitlines(), 1)
        if _PRIMARY.search(line) and not any(p.search(line) for p in ALLOWED)
    ]


def test_no_workflow_reads_the_account_variable_or_asks_for_a_switch():
    for path in _workflows():
        text = path.read_text(encoding="utf-8")
        assert "vars.CLAUDE_ACCOUNT" not in text, path.name
        assert "claude-account-switch" not in text, path.name
        assert "CLAUDE_ACCOUNT_SWITCH_TOKEN" not in text, path.name
    assert not (WORKFLOWS / "claude-account-switch.yml").exists()


def test_every_token_read_goes_through_the_picked_account():
    offenders = {}
    for path in _workflows():
        if path.name in INLINE:
            continue
        lines = _unexplained_reads(path.read_text(encoding="utf-8"))
        if lines:
            offenders[path.name] = lines
    assert not offenders, (
        "These lines read CLAUDE_CODE_OAUTH_TOKEN without the account the probe "
        f"picked, so they stay on an exhausted primary: {offenders}"
    )


def test_a_direct_read_is_caught():
    assert _unexplained_reads("token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}") == [1]
    picked = (
        "token: ${{ steps.claude_account.outputs.account == 'secondary' && "
        "secrets.CLAUDE_CODE_OAUTH_TOKEN_SECONDARY || secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    )
    assert _unexplained_reads(picked) == []


def _jobs():
    for path in _workflows():
        workflow = _load(path)
        for job_id, job in (workflow.get("jobs") or {}).items():
            yield path.name, workflow, job_id, job


def test_every_label_comes_from_a_probe_that_exists():
    """A misspelt step id or job output evaluates to '' and silently means primary."""
    used = 0
    for name, workflow, job_id, job in _jobs():
        steps = job.get("steps") or []
        text = yaml.safe_dump(job)
        if "steps.claude_account.outputs.account" in text:
            used += 1
            picks = [s for s in steps if s.get("id") == "claude_account"]
            assert len(picks) == 1, f"{name}:{job_id} reads a label it never picks"
            assert picks[0]["uses"] == "./.github/actions/claude-credential"
            reads = [
                i
                for i, s in enumerate(steps)
                if "steps.claude_account.outputs" in yaml.safe_dump(s)
            ]
            # A gate job only exports the label; any step reading it runs after.
            assert all(
                steps.index(picks[0]) < i for i in reads
            ), f"{name}:{job_id} reads the label before picking it"
        for gate in re.findall(r"needs\.([\w-]+)\.outputs\.claude_account", text):
            used += 1
            needs = job.get("needs") or []
            needs = [needs] if isinstance(needs, str) else needs
            assert gate in needs, f"{name}:{job_id} reads {gate} without needing it"
            exported = workflow["jobs"][gate]["outputs"]["claude_account"]
            assert exported == "${{ steps.claude_account.outputs.account }}", name
    assert used > 10, "the label checks matched almost nothing; the patterns drifted"


def test_the_probe_reports_labels_and_takes_tokens_by_env():
    action = yaml.load(ACTION.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    for output in action["outputs"].values():
        assert "token" not in output["value"].lower()
    for step in action["runs"]["steps"]:
        run = step.get("run", "")
        assert "inputs.primary-token" not in run and "inputs.secondary-token" not in run
        if "select_claude_account.py" in run:
            env = step["env"]
            assert env["CLAUDE_TOKEN_PRIMARY"] == "${{ inputs.primary-token }}"
            assert env["CLAUDE_TOKEN_SECONDARY"] == "${{ inputs.secondary-token }}"


def test_every_public_artifact_scan_covers_both_tokens():
    scans = 0
    for name, _workflow, job_id, job in _jobs():
        for step in job.get("steps") or []:
            if step.get("name") != "Refuse to upload a credential":
                continue
            scans += 1
            env = step.get("env") or {}
            assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == (
                "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
            ), f"{name}:{job_id}"
            assert env.get("CLAUDE_CODE_OAUTH_TOKEN_SECONDARY") == (
                "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_SECONDARY }}"
            ), f"{name}:{job_id}"
            assert (
                '"CLAUDE_CODE_OAUTH_TOKEN_SECONDARY"' in step["run"]
            ), f"{name}:{job_id}"
    assert scans >= 3


@pytest.mark.parametrize(
    "name", ["eval_scenario_lane.yml", "eval_flagship.yml", "test_gaia_agent_eval.yml"]
)
def test_the_backup_token_never_reaches_the_agent_under_test(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert "CLAUDE_CODE_OAUTH_TOKEN_BACKUP:" in text
    hidden = (
        '"CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "ANTHROPIC_API_KEY")' in text
        or "Remove-Item Env:\\CLAUDE_CODE_OAUTH_TOKEN_BACKUP" in text
    )
    assert hidden, f"{name} starts the agent with the backup token in its env"


def test_claude_run_fails_over_only_on_quota_wording():
    """Inline because it holds a fork's checkout; a local action would be the fork's."""
    workflow = _load(WORKFLOWS / "claude-run.yml")
    steps = workflow["jobs"]["run"]["steps"]
    assert not any("claude-credential" in str(s.get("uses", "")) for s in steps)
    by_id = {s.get("id"): s for s in steps if s.get("id")}
    assert by_id["a1"]["with"]["claude_code_oauth_token"] == (
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    )
    retry = by_id["b1"]
    assert retry["with"]["claude_code_oauth_token"] == (
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_SECONDARY }}"
    )
    assert "steps.quota.outputs.retry == 'true'" in retry["if"]
    assert "anthropic_api_key" not in retry["with"]
    assert "CREDENTIAL_RE" in by_id["quota"]["run"]
    # Never after model work: replies post through `gh` mid-run.
    assert ".modelUsage" in by_id["quota"]["run"]
    assert "total_cost_usd" in by_id["quota"]["run"]


def _quota_script() -> tuple[str, dict]:
    workflow = _load(WORKFLOWS / "claude-run.yml")
    job = workflow["jobs"]["run"]
    step = next(s for s in job["steps"] if s.get("id") == "quota")
    return step["run"], {k: job["env"][k] for k in ("QUOTA_RE", "CREDENTIAL_RE")}


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or not shutil.which("jq"),
    reason="runs the step under a POSIX bash with jq, as Actions does",
)
@pytest.mark.parametrize(
    "result, has_secondary, quota, warning",
    [
        # The weekly limit, worded: fail over.
        (
            {
                "type": "result",
                "is_error": True,
                "result": "You've hit your weekly limit · resets 4pm",
            },
            "true",
            "true",
            "using the backup",
        ),
        (
            {
                "type": "result",
                "is_error": True,
                "result": "Claude AI usage limit reached|1761062400",
            },
            "false",
            "true",
            "No backup Claude account",
        ),
        # A rejected token, or a bare refusal that could be one: never fail over.
        (
            {
                "type": "result",
                "is_error": True,
                "result": "Invalid API key · Please run /login",
            },
            "true",
            "false",
            None,
        ),
        ({"type": "result", "is_error": True, "modelUsage": {}}, "true", "false", None),
        # Throttling is not an empty account; a clean run is not refused.
        (
            {
                "type": "result",
                "is_error": True,
                "result": "API Error: 429 rate limit exceeded",
            },
            "true",
            "false",
            None,
        ),
        (
            {"type": "result", "is_error": False, "result": "done, quota tests added"},
            "true",
            "false",
            None,
        ),
    ],
    ids=["weekly", "no-secondary", "credential", "bare-refusal", "throttled", "clean"],
)
def test_claude_run_quota_step(tmp_path, result, has_secondary, quota, warning):
    import json
    import os
    import subprocess

    script, patterns = _quota_script()
    log = tmp_path / "exec.json"
    log.write_text(json.dumps([{"type": "system"}, result]), encoding="utf-8")
    step = tmp_path / "step.sh"
    step.write_text(script, encoding="utf-8", newline="\n")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        **patterns,
        "EXEC_FILE": str(log),
        "GITHUB_OUTPUT": str(output),
        "HAS_SECONDARY": has_secondary,
    }
    run = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", str(step)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert f"quota={quota}" in output.read_text(encoding="utf-8")
    if warning:
        assert warning in run.stdout
    else:
        assert "::warning" not in run.stdout


def test_claude_run_uses_the_shared_refusal_phrases():
    """claude-run.yml cannot import the list (it may hold a fork's checkout), so it
    carries a copy; this keeps the copy from drifting from the picker and runner."""
    from gaia.eval import claude_refusal

    env = _load(WORKFLOWS / "claude-run.yml")["jobs"]["run"]["env"]
    assert env["QUOTA_RE"] == claude_refusal.grep_pattern(claude_refusal.QUOTA_PHRASES)
    assert env["CREDENTIAL_RE"] == claude_refusal.grep_pattern(
        claude_refusal.CREDENTIAL_PHRASES
    )
    # The per-minute 429 rule: throttling wording never reads as quota.
    for phrase in claude_refusal.THROTTLE_PHRASES:
        assert not re.search(env["QUOTA_RE"], phrase, re.IGNORECASE), phrase
