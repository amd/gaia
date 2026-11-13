# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The eval workflows run one pinned Claude Code CLI, and its stderr cannot kill them.

v0.26.0-rc24 (run 43350541322) failed every smoke lane before a scenario ran: the
unpinned install took CLI 2.1.323, which prints a plugin warning on stderr, and the
preflight probe ran ``claude -p ... 2>&1`` under ``$ErrorActionPreference = "Stop"``
in Windows PowerShell 5.1, where any redirected native stderr line is a terminating
NativeCommandError. These tests hold both halves of the fix:

* every eval workflow installs the CLI through ``.github/actions/install-claude-cli``,
  whose default is one exact version, and nothing overrides it;
* the ``claude -p`` probe reads stderr as text, and its verdict still comes from the
  exit code. On Windows the real probe snippet is executed against a fake CLI.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
INSTALL_ACTION = REPO / ".github" / "actions" / "install-claude-cli" / "action.yml"
INSTALL_USES = "./.github/actions/install-claude-cli"
CREDENTIAL_USES = "./.github/actions/claude-credential"

#: Workflows whose `gaia eval agent` is driven and judged by the CLI.
EVAL_WORKFLOWS = [
    "eval_flagship.yml",
    "eval_scenario_lane.yml",
    "test_gaia_agent_eval.yml",
    "gaia_scorecard_refresh.yml",
]
#: Workflows holding a `claude -p` preflight probe in a PowerShell step.
PROBE_WORKFLOWS = ["eval_flagship.yml", "eval_scenario_lane.yml"]

_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_PLUGIN_WARNING = (
    'Plugin "frontend-design" is enabled in project settings but isn\'t installed'
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow: str):
    for job_id, job in (_load(WORKFLOWS / workflow).get("jobs") or {}).items():
        yield job_id, job.get("steps") or []


def _pin() -> str:
    return _load(INSTALL_ACTION)["inputs"]["version"]["default"]


def test_the_pin_is_one_exact_version():
    assert _EXACT_VERSION.match(_pin()), (
        f"install-claude-cli pins {_pin()!r}; a range or tag lets an untested CLI "
        "release into the eval"
    )


def test_the_action_installs_and_verifies_the_pinned_version():
    text = INSTALL_ACTION.read_text(encoding="utf-8")
    assert '"@anthropic-ai/claude-code@${CLI_VERSION}"' in text
    assert '"@anthropic-ai/claude-code@$want"' in text
    # A preinstalled claude ahead of npm's bin on PATH must fail, not run.
    assert text.count("Wanted Claude Code CLI") == 2


@pytest.mark.parametrize("workflow", EVAL_WORKFLOWS)
def test_eval_workflows_install_the_cli_only_through_the_pinned_action(workflow):
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    assert "@anthropic-ai/claude-code" not in text, (
        f"{workflow} installs the CLI inline; use {INSTALL_USES} so the eval runs "
        "the pinned version"
    )
    uses = [
        step
        for _, steps in _steps(workflow)
        for step in steps
        if step.get("uses") == INSTALL_USES
    ]
    assert uses, f"{workflow} no longer installs the Claude CLI"
    for step in uses:
        assert "version" not in (
            step.get("with") or {}
        ), f"{workflow} overrides the CLI pin; bump the action's default instead"


@pytest.mark.parametrize("workflow", EVAL_WORKFLOWS)
def test_the_pinned_cli_is_on_path_before_the_account_probe(workflow):
    """Otherwise claude-credential installs its own default version first."""
    for job_id, steps in _steps(workflow):
        uses = [step.get("uses") for step in steps]
        if CREDENTIAL_USES not in uses:
            continue
        assert INSTALL_USES in uses, f"{workflow}:{job_id} never installs the CLI"
        assert uses.index(INSTALL_USES) < uses.index(
            CREDENTIAL_USES
        ), f"{workflow}:{job_id} probes the accounts before installing the pinned CLI"


def test_no_workflow_installs_the_cli_unpinned_for_an_eval():
    """Any workflow running `gaia eval agent` names an exact CLI version."""
    unpinned = re.compile(r"@anthropic-ai/claude-code(?!@\d+\.\d+\.\d+\b)")
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if "eval agent" not in text:
            continue
        offenders += [
            f"{path.name}: {line.strip()}"
            for line in text.splitlines()
            if "npm install" in line and unpinned.search(line)
        ]
    assert not offenders, offenders


def _probe_snippet(workflow: str) -> str:
    """The probe lines, from the switch to "Continue" through the restore of "Stop"."""
    lines = (WORKFLOWS / workflow).read_text(encoding="utf-8").splitlines()
    call = next(i for i, line in enumerate(lines) if "claude -p --model" in line)
    start = max(
        i
        for i in range(call)
        if lines[i].strip() == '$ErrorActionPreference = "Continue"'
    )
    end = next(
        i
        for i in range(call, len(lines))
        if lines[i].strip() == '$ErrorActionPreference = "Stop"'
    )
    assert (
        call - start < 5 and end - call < 5
    ), f"{workflow}: the probe is not wrapped tightly in Continue ... Stop"
    return "\n".join(line.strip() for line in lines[start : end + 1])


@pytest.mark.parametrize("workflow", PROBE_WORKFLOWS)
def test_the_probe_reads_stderr_as_text_and_cannot_pass_by_not_running(workflow):
    snippet = _probe_snippet(workflow)
    assert "$global:LASTEXITCODE = 1" in snippet
    assert "2>&1" in snippet and "Out-String" in snippet
    assert "$probeExit = $LASTEXITCODE" in snippet
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    assert "util/classify_claude_probe.py --exit-code $probeExit" in text


@pytest.mark.parametrize("workflow", PROBE_WORKFLOWS)
def test_no_claude_call_runs_under_stop_with_stderr_redirected(workflow):
    """The rc24 shape: `claude ... 2>&1` outside a Continue window."""
    for _, steps in _steps(workflow):
        for step in steps:
            preference = None
            for line in (step.get("run") or "").splitlines():
                stripped = line.strip()
                m = re.match(r'^\$ErrorActionPreference = "(\w+)"', stripped)
                if m:
                    preference = m.group(1)
                elif re.search(r"(^|[(&|]\s*)claude\s.*2>&1", stripped):
                    assert (
                        preference == "Continue"
                    ), f"{workflow} step {step.get('name')!r}: {stripped}"


_POWERSHELL = shutil.which("powershell.exe") if sys.platform == "win32" else None
_FAKE_CLAUDES = {
    "warns_then_answers": (
        f"@echo off\r\n>&2 echo {_PLUGIN_WARNING}\r\necho ok\r\nexit /b 0\r\n"
    ),
    "rejected": (
        f"@echo off\r\n>&2 echo {_PLUGIN_WARNING}\r\n"
        "echo Invalid API key\r\nexit /b 1\r\n"
    ),
}


def _run_probe(workflow: str, tmp_path: Path, fake: str | None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if fake:
        (bin_dir / "claude.cmd").write_text(_FAKE_CLAUDES[fake], encoding="ascii")
    script = tmp_path / "probe.ps1"
    script.write_text(
        '$ErrorActionPreference = "Stop"\n'
        f"{_probe_snippet(workflow)}\n"
        'Write-Output "EXIT=$probeExit"\n'
        'Write-Output "EAP=$ErrorActionPreference"\n'
        'Write-Output "BEGIN"\n'
        "Write-Output $probe\n",
        encoding="ascii",
    )
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    env = {**os.environ, "PATH": f"{bin_dir};{system32}", "EVAL_MODEL": "m"}
    proc = subprocess.run(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
        + ["-File", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"the probe snippet itself died:\n{proc.stderr}"
    head, _, probe = proc.stdout.partition("BEGIN")
    return head, probe


@pytest.mark.skipif(not _POWERSHELL, reason="needs Windows PowerShell 5.1")
@pytest.mark.parametrize("workflow", PROBE_WORKFLOWS)
def test_a_cli_stderr_warning_does_not_kill_the_probe(workflow, tmp_path):
    head, probe = _run_probe(workflow, tmp_path, "warns_then_answers")
    assert "EXIT=0" in head and "EAP=Stop" in head
    assert "ok" in probe and "frontend-design" in probe


@pytest.mark.skipif(not _POWERSHELL, reason="needs Windows PowerShell 5.1")
@pytest.mark.parametrize("workflow", PROBE_WORKFLOWS)
def test_a_rejected_probe_still_reports_its_exit_code(workflow, tmp_path):
    head, probe = _run_probe(workflow, tmp_path, "rejected")
    assert "EXIT=1" in head and "EAP=Stop" in head
    assert "Invalid API key" in probe


@pytest.mark.skipif(not _POWERSHELL, reason="needs Windows PowerShell 5.1")
@pytest.mark.parametrize("workflow", PROBE_WORKFLOWS)
def test_a_missing_cli_reads_as_a_failure_not_a_pass(workflow, tmp_path):
    head, _ = _run_probe(workflow, tmp_path, None)
    assert "EXIT=1" in head
