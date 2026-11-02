# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/check_release_assets.py: a release carries every installer it promises.

v0.25.3 is the case this exists for: its publish run was green, and its combined
Windows setup and terminal packages never reached the release because the attach
job failed in another workflow. The asset lists below are the real ones, so these
tests fail if the gate would have passed that release or would refuse v0.25.2.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_release_assets as cra  # noqa: E402
from release_tag import parse_tag  # noqa: E402

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"
FINAL_ONLY_SETUP = (
    "(needs.validate.outputs.is_rc == 'true' || "
    "needs.windows-setup.outputs.ok == 'true')"
)

TUI = [
    "gaia-darwin-amd64",
    "gaia-darwin-arm64",
    "gaia-linux-amd64",
    "gaia-linux-arm64",
    "gaia-tui-SHA256SUMS.txt",
    "gaia-windows-amd64.exe",
    "gaia-windows-arm64.exe",
]
FEEDS = ["latest-linux.yml", "latest-mac.yml", "latest.yml"]


def _desktop(version: str) -> list[str]:
    ui = f"gaia-agent-ui-{version}"
    return [
        f"{ui}-amd64.deb",
        f"{ui}-arm64.dmg",
        f"{ui}-arm64.dmg.blockmap",
        f"{ui}-x64-setup.exe",
        f"{ui}-x64-setup.exe.blockmap",
        f"{ui}-x86_64.AppImage",
    ]


def _combined(version: str) -> list[str]:
    return [
        f"gaia-{version}-darwin-arm64.pkg",
        f"gaia-{version}-darwin-x64.pkg",
        f"gaia-{version}-win-x64-setup.exe",
        f"gaia-{version}.x86_64.rpm",
        "gaia-installers-SHA256SUMS.txt",
        f"gaia_{version}_amd64.deb",
    ]


# What each release really carried, less its Python and flagship agent files.
V0_25_2 = _desktop("0.25.2") + _combined("0.25.2") + TUI + FEEDS
V0_25_3 = _desktop("0.25.3") + TUI + FEEDS
V0_25_4_RC1 = _desktop("0.25.4-rc.1") + _combined("0.25.4-rc.1") + TUI + FEEDS


@pytest.fixture(autouse=True)
def every_platform_has_an_agent(monkeypatch):
    # darwin-x64 is best-effort, so a valid lock PR may drop it from the real lock.
    monkeypatch.setattr(cra.artifacts, "platform_has_sidecar", lambda p: True)


def _assets(names: list[str]) -> list[dict]:
    return [{"name": n, "size": 1024, "state": "uploaded"} for n in names]


def _missing(tag: str, names: list[str], **kwargs) -> list[str]:
    promised = cra.promised_assets(parse_tag(tag), **kwargs)
    return cra.missing_assets(promised, _assets(names))


def test_v0_25_3_is_refused_and_every_lost_file_is_named():
    assert sorted(_missing("v0.25.3", V0_25_3)) == sorted(_combined("0.25.3"))


@pytest.mark.parametrize(
    "tag, names", [("v0.25.2", V0_25_2), ("v0.25.4-rc1", V0_25_4_RC1)]
)
def test_a_complete_release_passes(tag, names):
    assert _missing(tag, names) == []


@pytest.mark.parametrize("gone", V0_25_2)
def test_any_single_missing_file_fails(gone):
    assert _missing("v0.25.2", [n for n in V0_25_2 if n != gone]) == [gone]


def test_a_failed_or_empty_upload_does_not_count():
    assets = _assets(V0_25_2)
    assets[0]["state"] = "starter"
    assets[1]["size"] = 0
    promised = cra.promised_assets(parse_tag("v0.25.2"))
    assert sorted(cra.missing_assets(promised, assets)) == sorted(V0_25_2[:2])


def test_a_final_follows_its_flagship_lock(monkeypatch):
    monkeypatch.setattr(
        cra.artifacts, "platform_has_sidecar", lambda p: p != "darwin-x64"
    )
    names = [n for n in V0_25_2 if "darwin-x64" not in n]
    assert _missing("v0.25.2", names) == []


def test_an_rc_needs_every_package_whatever_the_lock_says(monkeypatch):
    monkeypatch.setattr(cra.artifacts, "platform_has_sidecar", lambda p: False)
    gone = "gaia-0.25.4-rc.1-darwin-x64.pkg"
    assert _missing("v0.25.4-rc1", [n for n in V0_25_4_RC1 if n != gone]) == [gone]


def test_an_rc_whose_setup_failed_is_only_excused_the_setup():
    setup = "gaia-0.25.4-rc.1-win-x64-setup.exe"
    names = [n for n in V0_25_4_RC1 if n != setup]
    assert _missing("v0.25.4-rc1", names) == [setup]
    assert _missing("v0.25.4-rc1", names, windows_setup=False) == []


def _run(monkeypatch, capsys, names: list[str], *argv: str) -> tuple[int, str]:
    calls = []

    def gh(cmd, **kwargs):
        calls.append(cmd)
        out = json.dumps({"assets": _assets(names)})
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(cra.subprocess, "run", gh)
    code = cra.main(["--repo", "amd/gaia", *argv])
    assert calls[0][:3] == ["gh", "release", "view"]
    assert calls[0][3] == argv[1]
    return code, capsys.readouterr().out


def test_the_cli_fails_and_names_the_files(monkeypatch, capsys):
    code, out = _run(monkeypatch, capsys, V0_25_3, "--tag", "v0.25.3")
    assert code == 1
    assert out.startswith("::error::release v0.25.3 is missing 6 of ")
    assert "gaia-0.25.3-win-x64-setup.exe" in out
    assert "'Attach terminal installers'" in out and "'GitHub Release'" in out


def test_the_cli_passes_a_complete_release(monkeypatch, capsys):
    code, out = _run(monkeypatch, capsys, V0_25_2, "--tag", "v0.25.2")
    assert code == 0 and "v0.25.2" in out


def test_a_final_cannot_skip_the_windows_setup():
    with pytest.raises(SystemExit, match="final"):
        cra.main(["--repo", "amd/gaia", "--tag", "v0.25.3", "--skip-windows-setup"])


def test_an_unreadable_release_fails_instead_of_passing(monkeypatch):
    monkeypatch.setattr(
        cra.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "release not found"),
    )
    with pytest.raises(SystemExit, match="release not found"):
        cra.main(["--repo", "amd/gaia", "--tag", "v0.25.3"])


# ── publish.yml wires it in ──────────────────────────────────────────────────


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


@pytest.mark.parametrize("job", ["approve-publish", "github-release"])
def test_a_final_publishes_nothing_without_its_windows_setup(jobs, job):
    assert "windows-setup" in jobs[job]["needs"]
    assert FINAL_ONLY_SETUP in jobs[job]["if"]


def test_nothing_publishes_ahead_of_the_approval_gate(jobs):
    for job in ("publish-pypi", "publish-npm"):
        assert "approve-publish" in jobs[job]["needs"]


def test_the_check_runs_after_the_attach_even_if_it_failed(jobs):
    job = jobs["release-assets-complete"]
    assert "github-release-installers" in job["needs"]
    assert "!cancelled()" in job["if"]
    assert "needs.github-release.result == 'success'" in job["if"]
    assert "github-release-installers" not in job["if"]
    assert "environment" not in job
    assert "continue-on-error" not in job


def test_the_step_only_excuses_an_rc(jobs):
    (step,) = [s for s in jobs["release-assets-complete"]["steps"] if "run" in s]
    run = step["run"]
    assert "util/check_release_assets.py" in run and "--skip-windows-setup" in run
    assert '[ "${IS_RC}" = "true" ] && [ "${WINDOWS_SETUP_OK}" != "true" ]' in run
    assert "||" not in run


@pytest.mark.parametrize(
    "is_rc, windows_ok, skipped",
    [
        ("false", "true", False),
        ("false", "", False),
        ("true", "true", False),
        ("true", "", True),
    ],
)
def test_the_step_passes_the_skip_flag_only_for_an_rc_without_a_setup(
    tmp_path, jobs, is_rc, windows_ok, skipped
):
    bash = shutil.which("bash")
    if sys.platform == "win32" or not bash:
        pytest.skip("bash on Windows may resolve to WSL; the step runs on Linux")
    version = subprocess.run(
        [bash, "-c", 'echo "${BASH_VERSINFO[0]}${BASH_VERSINFO[1]}"'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if int(version) < 44:
        pytest.skip("an empty array under set -u needs bash 4.4+, as on the runner")
    stub = tmp_path / "python3"
    stub.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/args"\n', encoding="utf-8"
    )
    stub.chmod(0o755)
    (step,) = [s for s in jobs["release-assets-complete"]["steps"] if "run" in s]
    env = dict(
        os.environ,
        PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        GITHUB_REPOSITORY="amd/gaia",
        TAG_NAME="v0.25.4-rc1",
        IS_RC=is_rc,
        WINDOWS_SETUP_OK=windows_ok,
    )
    subprocess.run([bash, "-c", step["run"]], env=env, check=True)
    args = (tmp_path / "args").read_text(encoding="utf-8").splitlines()
    assert ("--skip-windows-setup" in args) is skipped
    assert args[:3] == ["util/check_release_assets.py", "--repo", "amd/gaia"]


def test_the_binaries_match_the_ones_the_release_checksums(jobs):
    (step,) = [
        s
        for s in jobs["github-release"]["steps"]
        if s.get("name") == "Checksum TUI binaries"
    ]
    for name in cra.TUI_FILES:
        assert name in step["run"]
