# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A release candidate ships the terminal's .pkg/.deb/.rpm; a final ships them as before.

Release candidates used to carry only the raw terminal binaries, so the
installers a final ships were first exercised by the final itself. publish.yml
now builds them for an RC around that RC's own flagship agent, refuses to
publish unless every leg installed cleanly, and attaches them to the
pre-release under the names a final uses.

These are configuration assertions plus one run of the attach script against a
stubbed ``gh``: the publish path cannot be dry-run, so what it would upload is
proven here instead.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
sys.path.insert(0, str(REPO_ROOT / "installer" / "tui"))

import artifacts  # noqa: E402

IS_RC = "needs.validate.outputs.is_rc == 'true'"
NOT_RC = "needs.validate.outputs.is_rc != 'true'"
ATTACH_JOB = "rc-github-release-installers"
RC_VERSION = "0.25.1-rc.4"
PACKAGE_PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-x64")


def _jobs(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]


def _triggers(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    return data.get("on", data.get(True))


def _step(job: dict, name: str) -> dict:
    (step,) = [s for s in job["steps"] if s.get("name") == name]
    return step


@pytest.fixture(scope="module")
def publish() -> dict:
    return _jobs("publish.yml")


def _package_names(version: str) -> list[str]:
    return [
        n for p in PACKAGE_PLATFORMS for n, _ in artifacts.artifacts_for(p, version)
    ]


# ── The build ────────────────────────────────────────────────────────────────


def test_an_rc_builds_the_packages_around_its_own_flagship(publish):
    tui = publish["rc-tui"]
    assert tui["uses"] == "./.github/workflows/tui_installers.yml"
    assert IS_RC in tui["if"] and "!cancelled()" in tui["if"]
    # The agent THIS run froze, never the hub's -- same as rc-windows-setup.
    assert tui["with"]["sidecar"] == "artifact"
    assert "rc-flagship" in tui["needs"]
    assert "needs.rc-flagship.result == 'success'" in tui["if"]
    assert tui["with"].get("installers", True) is True
    # -rc.N in every file name, like the Windows setup and the desktop app.
    assert tui["with"]["version"] == "${{ needs.validate.outputs.npm_version }}"
    # No prefix: windows_setup.yml reads the unprefixed tui-binaries.
    assert not tui["with"].get("artifact_prefix")


def test_nothing_publishes_unless_all_four_rc_packages_installed(publish):
    gate = f"({NOT_RC} || needs.rc-tui-complete.result == 'success')"
    for job in ("approve-publish", "github-release"):
        assert "rc-tui-complete" in publish[job]["needs"]
        assert gate in publish[job]["if"], f"{job} does not require rc-tui-complete"

    complete = publish["rc-tui-complete"]
    assert complete["needs"] == ["validate", "rc-tui"]
    assert IS_RC in complete["if"]
    # Every leg passed its clean-runner install...
    assert "needs.rc-tui.outputs.installers_ok == 'true'" in complete["if"]
    # ...and each of the four files exists, by name (an Intel .pkg is skipped
    # without failing a leg when its best-effort agent did not build).
    run = _step(complete, "Every terminal installer is present")["run"]
    assert "for platform in darwin-arm64 darwin-x64 linux-x64" in run
    assert "artifacts.py names" in run and "exit 1" in run


def test_installers_ok_means_every_leg_smoke_tested():
    callee = _jobs("tui_installers.yml")
    assert callee["installers-ok"]["needs"] == "tui-installers-smoke"
    assert "if" not in callee["installers-ok"], (
        "installers-ok must run only when every smoke leg passed; an if: would let "
        "it report ok for a partial set"
    )


# ── The attach ───────────────────────────────────────────────────────────────


def test_the_attach_job_runs_for_an_rc_after_its_prerelease_exists(publish):
    job = publish[ATTACH_JOB]
    assert IS_RC in job["if"]
    assert "needs.github-release.result == 'success'" in job["if"]
    assert "needs.rc-tui-complete.result == 'success'" in job["if"]
    assert job["permissions"] == {"contents": "write"}
    assert "environment" not in job
    (download,) = [
        s
        for s in job["steps"]
        if s.get("uses", "").startswith("actions/download-artifact")
        and s["with"].get("pattern") == "tui-installers-*"
    ]
    assert download["with"]["merge-multiple"] is True


def test_the_attach_is_idempotent_and_github_only(publish):
    run = _step(publish[ATTACH_JOB], "Attach the installers and their checksums")["run"]
    assert "gh release upload" in run and "--clobber" in run
    assert "gaia-installers-SHA256SUMS.txt" in run
    text = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    assert "publish_to_r2" not in text and "GAIA_HUB" not in text


def test_the_final_release_job_is_unchanged(publish):
    """The steps a final's GitHub Release runs, pinned. RC work is gated IS_RC."""
    final_steps = [
        s.get("name") or s.get("uses")
        for s in publish["github-release"]["steps"]
        if IS_RC not in s.get("if", "")
    ]
    assert final_steps == [
        "actions/checkout@v7",
        "Download Python distributions",
        "Sign with Sigstore",
        "Extract release notes",
        "Download TUI binaries",
        "Checksum TUI binaries",
        "Download Desktop Installer (Windows NSIS)",
        "Download Desktop Installer (macOS DMG)",
        "Download Desktop Installer (Linux DEB + AppImage)",
        "Create GitHub Release",
    ]


def test_a_final_still_gets_its_installers_from_release_components():
    filters = _triggers("release_components.yml")["push"]["tags"]
    assert "v*" in filters and "!v*-rc*" in filters
    jobs = _jobs("release_components.yml")
    tui = jobs["tui"]
    assert tui["uses"] == "./.github/workflows/tui_installers.yml"
    assert tui["with"]["sidecar"] == "pinned"
    assert "installers" not in tui["with"]
    attach = _step(
        jobs["github-release-installers"], "Attach the installers and their checksums"
    )["run"]
    assert "gh release upload" in attach and "--clobber" in attach


def test_rc_notes_name_the_packages_they_attach(publish):
    run = _step(publish["github-release"], "Extract release notes (release candidate)")[
        "run"
    ]
    for name in _package_names("@NPM_VERSION@"):
        assert f"`{name}`" in run, f"the RC notes do not list {name}"
    assert "gaia-installers-SHA256SUMS.txt" in run
    assert "ship with the final release" not in run


# ── The attach script, run against a stubbed gh ──────────────────────────────


def _bash() -> str:
    if sys.platform == "win32":
        pytest.skip("bash on Windows may resolve to WSL; the attach runs on Linux")
    bash = shutil.which("bash")
    if not bash or not shutil.which("sha256sum"):
        pytest.skip("needs bash 4+ and sha256sum, as on the ubuntu runner")
    major = subprocess.run(
        [bash, "-c", "echo ${BASH_VERSINFO[0]}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if int(major) < 4:
        pytest.skip("the attach uses mapfile (bash 4+), as on the ubuntu runner")
    return bash


def _summed(path: Path) -> list[str]:
    """File names in a sha256sum listing (`<hash>  <name>`)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    assert all(re.match(r"^[0-9a-f]{64} [ *]\S", ln) for ln in lines), lines
    return sorted(ln[66:] for ln in lines)


def _exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _workspace(tmp_path: Path, present: list[str]) -> tuple[Path, dict]:
    ws = tmp_path / "ws"
    tui = ws / "installer" / "tui"
    tui.mkdir(parents=True)
    for f in ("artifacts.py", "fetch_sidecar.py"):
        shutil.copy(REPO_ROOT / "installer" / "tui" / f, tui / f)
    (ws / "dist").mkdir()
    for n in present:
        (ws / "dist" / n).write_bytes(f"bytes of {n}".encode())

    stub = tmp_path / "bin"
    stub.mkdir()
    _exe(stub / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    _exe(
        stub / "gh",
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/gh.args"\n',
    )
    env = dict(os.environ)
    env.update(
        PATH=f"{stub}{os.pathsep}{env['PATH']}",
        VERSION=RC_VERSION,
        TAG="v0.25.1-rc4",
        GITHUB_REPOSITORY="amd/gaia",
        GH_TOKEN="stub",
    )
    return ws, env


def _run_attach(bash: str, ws: Path, env: dict, publish: dict, windows_ok: str):
    job = publish[ATTACH_JOB]
    env = dict(env, WINDOWS_SETUP_OK=windows_ok)
    listed = subprocess.run(
        [
            bash,
            "-c",
            _step(job, "List the installers and write their checksums")["run"],
        ],
        cwd=ws,
        env=env,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        return listed
    return subprocess.run(
        [bash, "-c", _step(job, "Attach the installers and their checksums")["run"]],
        cwd=ws,
        env=env,
        capture_output=True,
        text=True,
    )


def test_the_attach_uploads_exactly_the_four_packages_and_their_sums(tmp_path, publish):
    bash = _bash()
    packages = _package_names(RC_VERSION)
    assert len(packages) == 4
    setup = artifacts.artifacts_for("win-x64", RC_VERSION)[0][0]
    ws, env = _workspace(tmp_path, packages + [setup, "gaia-agent-linux-x64"])

    done = _run_attach(bash, ws, env, publish, windows_ok="true")
    assert done.returncode == 0, done.stdout + done.stderr

    args = (tmp_path / "gh.args").read_text(encoding="utf-8").splitlines()
    assert args[:3] == ["release", "upload", "v0.25.1-rc4"]
    uploaded = [a for a in args if a.startswith("dist/")]
    assert uploaded == [f"dist/{n}" for n in packages] + [
        "dist/gaia-installers-SHA256SUMS.txt"
    ]
    assert "--clobber" in args and args[args.index("--repo") + 1] == "amd/gaia"

    # The final's file covers the Windows setup too, so this one does.
    sums = _summed(ws / "dist" / "gaia-installers-SHA256SUMS.txt")
    assert sums == sorted(packages + [setup])


def test_the_sums_skip_a_windows_setup_that_did_not_build(tmp_path, publish):
    bash = _bash()
    packages = _package_names(RC_VERSION)
    ws, env = _workspace(tmp_path, packages)
    done = _run_attach(bash, ws, env, publish, windows_ok="")
    assert done.returncode == 0, done.stdout + done.stderr
    sums = _summed(ws / "dist" / "gaia-installers-SHA256SUMS.txt")
    assert sums == sorted(packages)


@pytest.mark.parametrize("drop", range(4))
def test_a_missing_package_attaches_nothing_and_says_which(tmp_path, publish, drop):
    bash = _bash()
    packages = _package_names(RC_VERSION)
    gone = packages.pop(drop)
    ws, env = _workspace(tmp_path, packages)

    done = _run_attach(bash, ws, env, publish, windows_ok="")
    assert done.returncode != 0
    assert gone in done.stdout and "::error::" in done.stdout
    assert not (tmp_path / "gh.args").exists(), "gh ran despite a missing package"


@pytest.mark.parametrize("drop", [None, 0, 1, 2, 3])
def test_the_prepublish_gate_fails_short_of_any_package(tmp_path, publish, drop):
    bash = _bash()
    packages = _package_names(RC_VERSION)
    gone = packages.pop(drop) if drop is not None else None
    ws, env = _workspace(tmp_path, packages)
    run = _step(publish["rc-tui-complete"], "Every terminal installer is present")[
        "run"
    ]
    done = subprocess.run(
        [bash, "-c", run], cwd=ws, env=env, capture_output=True, text=True
    )
    if gone is None:
        assert done.returncode == 0, done.stdout + done.stderr
    else:
        assert done.returncode != 0
        assert gone in done.stdout and "::error::" in done.stdout


# ── The version inside the packages ──────────────────────────────────────────


@pytest.mark.parametrize(
    "version, expected",
    [
        ("0.25.1", "0.25.1"),
        ("0.25.1-rc.4", "0.25.1~rc.4"),
        ("0.25.1-rc.10", "0.25.1~rc.10"),
    ],
)
def test_linux_package_version(version, expected):
    bash = _bash()
    out = subprocess.run(
        [
            bash,
            str(REPO_ROOT / "installer/tui/linux/build-packages.sh"),
            "--version",
            version,
            "--print-package-version",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == expected


@pytest.mark.parametrize("version", ["0.25.1-rc4", "0.25.1-rc.0", "0.25.1-beta.1"])
def test_other_prerelease_spellings_are_refused(version):
    bash = _bash()
    out = subprocess.run(
        [
            bash,
            str(REPO_ROOT / "installer/tui/linux/build-packages.sh"),
            "--version",
            version,
            "--print-package-version",
        ],
        capture_output=True,
        text=True,
    )
    assert out.returncode != 0
    assert "not a valid version" in out.stderr
