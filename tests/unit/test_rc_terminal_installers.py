# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every release tag, final or RC, ships the terminal's .pkg/.deb/.rpm from publish.yml.

Release candidates used to carry only the raw terminal binaries, and a final got
its installers from release_components.yml, behind the Agent Hub approval -- so
a final could sit for hours with no Windows setup. publish.yml now builds them
for every tag (an RC around its own flagship agent, a final around the one its
lock pins), refuses to publish unless every leg installed cleanly, and attaches
them with no approval of their own.

These are configuration assertions plus one run of the attach script against a
stubbed ``gh``: the publish path cannot be dry-run, so what it would upload is
proven here instead.
"""

from __future__ import annotations

import json
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
ATTACH_JOB = "github-release-installers"
RC_VERSION = "0.25.1-rc.4"
FINAL_VERSION = "0.25.1"
PACKAGE_PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-x64")
LOCK = Path("hub/agents/gaia/npm/binaries.lock.json")
SIDECAR = "${{ needs.validate.outputs.is_rc == 'true' && 'artifact' || 'pinned' }}"


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


def test_every_tag_builds_the_packages_around_its_flagship(publish):
    tui = publish["tui-installers"]
    assert tui["uses"] == "./.github/workflows/tui_installers.yml"
    assert "!cancelled()" in tui["if"]
    # A final builds them too: no RC-only gate, and no approval environment.
    assert IS_RC not in tui["if"]
    assert "environment" not in tui
    # An RC wraps the agent THIS run froze; a final the one its lock pins.
    assert tui["with"]["sidecar"] == SIDECAR
    assert "rc-flagship" in tui["needs"]
    assert f"({NOT_RC} || needs.rc-flagship.result == 'success')" in tui["if"]
    assert tui["with"].get("installers", True) is True
    # -rc.N in every file name, like the Windows setup and the desktop app.
    assert tui["with"]["version"] == "${{ needs.validate.outputs.npm_version }}"
    # No prefix: windows_setup.yml reads the unprefixed tui-binaries.
    assert not tui["with"].get("artifact_prefix")


def test_nothing_publishes_unless_every_package_installed(publish):
    gate = "needs.tui-installers-complete.result == 'success'"
    for job in ("approve-publish", "github-release"):
        assert "tui-installers-complete" in publish[job]["needs"]
        assert gate in publish[job]["if"], f"{job} does not require the packages"
        # For a final as well as an RC.
        assert f"{NOT_RC} || {gate}" not in publish[job]["if"]

    complete = publish["tui-installers-complete"]
    assert complete["needs"] == ["validate", "tui-installers"]
    assert IS_RC not in complete["if"]
    # Every leg passed its clean-runner install...
    assert "needs.tui-installers.outputs.installers_ok == 'true'" in complete["if"]
    # ...and each file exists, by name (an Intel .pkg is skipped without
    # failing a leg when there is no Intel agent to wrap).
    run = _step(complete, "Every terminal installer is present")["run"]
    assert "for platform in darwin-arm64 darwin-x64 linux-x64" in run
    assert "artifacts.py names" in run and "exit 1" in run
    # A final is held to the platforms its flagship lock has an agent for.
    assert "artifacts.py publish-args" in run
    assert "--exclude-platform win-x64" in run


def test_installers_ok_means_every_leg_smoke_tested():
    callee = _jobs("tui_installers.yml")
    assert callee["installers-ok"]["needs"] == "tui-installers-smoke"
    assert "if" not in callee["installers-ok"], (
        "installers-ok must run only when every smoke leg passed; an if: would let "
        "it report ok for a partial set"
    )


# ── The attach ───────────────────────────────────────────────────────────────


def test_the_attach_job_runs_for_every_tag_after_its_release_exists(publish):
    job = publish[ATTACH_JOB]
    assert IS_RC not in job["if"] and NOT_RC not in job["if"]
    assert "needs.github-release.result == 'success'" in job["if"]
    assert "needs.tui-installers-complete.result == 'success'" in job["if"]
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


def test_the_final_release_job_steps(publish):
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
        "Download the combined Windows setup",
        "Check the combined Windows setup is this release's",
        "Download Desktop Installer (Windows NSIS)",
        "Download Desktop Installer (macOS DMG)",
        "Download Desktop Installer (Linux DEB + AppImage)",
        "Create GitHub Release with bounded rate-limit retry",
    ]


def test_a_final_attaches_every_installer_from_publish_without_approval(publish):
    """A final's Windows setup and packages never wait on a second approval."""
    for job_id in (
        "tui-installers",
        "tui-installers-complete",
        "windows-setup",
        "github-release",
        ATTACH_JOB,
        "redeploy-website",
    ):
        job = publish[job_id]
        assert "environment" not in job, f"{job_id} waits on an environment"
        assert IS_RC not in job.get("if", ""), f"{job_id} runs for an RC only"
    # approve-publish is the final's one approval; pypi and npm are the OIDC
    # trusted-publisher environments, which carry no reviewers.
    gated = sorted(j for j, job in publish.items() if "environment" in job)
    assert gated == ["approve-publish", "publish-npm", "publish-pypi"]

    setup = publish["windows-setup"]
    assert setup["uses"] == "./.github/workflows/windows_setup.yml"
    assert setup["with"]["sidecar"] == SIDECAR
    # github-release attaches the setup for a final too.
    (download,) = [
        s
        for s in publish["github-release"]["steps"]
        if s.get("name") == "Download the combined Windows setup"
    ]
    assert download["if"] == "needs.windows-setup.outputs.ok == 'true'"
    assert download["with"]["path"] == "release-assets"


def test_release_components_no_longer_attaches_installers_on_a_tag():
    filters = _triggers("release_components.yml")["push"]["tags"]
    assert "v*" in filters and "!v*-rc*" in filters
    jobs = _jobs("release_components.yml")
    dispatch_only = "github.event_name == 'workflow_dispatch'"
    for job_id in ("windows-setup", "windows-setup-missing", ATTACH_JOB):
        assert dispatch_only in jobs[job_id]["if"], f"{job_id} still runs on a tag"
    # The six raw binaries still build on a tag for the hub; the packages don't.
    tui = jobs["tui"]
    assert tui["uses"] == "./.github/workflows/tui_installers.yml"
    assert tui["with"]["sidecar"] == "pinned"
    assert tui["with"]["installers"] == "${{ " + dispatch_only + " }}"
    # A dispatch embeds the published release's desktop app, never a run's.
    assert "agent_ui_publish_run_sha" not in jobs["windows-setup"]["with"]
    attach = _step(jobs[ATTACH_JOB], "Attach the installers and their checksums")["run"]
    assert "gh release upload" in attach and "--clobber" in attach
    # The hub publish and its approval are untouched.
    hub = jobs["terminal-hub"]
    assert "agent-publish" in hub["environment"]
    assert dispatch_only not in hub["if"]


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


def _workspace(
    tmp_path: Path,
    present: list[str],
    version: str = RC_VERSION,
    drop_lock_platform: str | None = None,
) -> tuple[Path, dict]:
    ws = tmp_path / "ws"
    tui = ws / "installer" / "tui"
    tui.mkdir(parents=True)
    for f in ("artifacts.py", "fetch_sidecar.py"):
        shutil.copy(REPO_ROOT / "installer" / "tui" / f, tui / f)
    # A final reads which platforms have an agent from the committed lock.
    lock = json.loads((REPO_ROOT / LOCK).read_text(encoding="utf-8"))
    if drop_lock_platform:
        del lock["components"]["sidecar"]["platforms"][drop_lock_platform]
    (ws / LOCK).parent.mkdir(parents=True)
    (ws / LOCK).write_text(json.dumps(lock), encoding="utf-8")
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
        VERSION=version,
        IS_RC="true" if version == RC_VERSION else "false",
        TAG="v0.25.1-rc4" if version == RC_VERSION else "v0.25.1",
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
    run = _step(
        publish["tui-installers-complete"], "Every terminal installer is present"
    )["run"]
    done = subprocess.run(
        [bash, "-c", run], cwd=ws, env=env, capture_output=True, text=True
    )
    if gone is None:
        assert done.returncode == 0, done.stdout + done.stderr
    else:
        assert done.returncode != 0
        assert gone in done.stdout and "::error::" in done.stdout


# ── A final: the platforms its flagship lock has an agent for ───────────────


def _final_packages(skip: str | None = None) -> list[str]:
    return [
        n
        for p in PACKAGE_PLATFORMS
        if p != skip
        for n, _ in artifacts.artifacts_for(p, FINAL_VERSION)
    ]


def test_a_final_attaches_its_packages_and_the_setup_sums(tmp_path, publish):
    bash = _bash()
    packages = _final_packages()
    setup = artifacts.artifacts_for("win-x64", FINAL_VERSION)[0][0]
    ws, env = _workspace(tmp_path, packages + [setup], version=FINAL_VERSION)

    done = _run_attach(bash, ws, env, publish, windows_ok="true")
    assert done.returncode == 0, done.stdout + done.stderr

    args = (tmp_path / "gh.args").read_text(encoding="utf-8").splitlines()
    assert args[:3] == ["release", "upload", "v0.25.1"]
    uploaded = sorted(a for a in args if a.startswith("dist/") and "SHA256" not in a)
    assert uploaded == sorted(f"dist/{n}" for n in packages)
    assert "dist/gaia-installers-SHA256SUMS.txt" in args
    sums = _summed(ws / "dist" / "gaia-installers-SHA256SUMS.txt")
    assert sums == sorted(packages + [setup])


def test_a_final_with_no_intel_agent_ships_without_the_intel_pkg(tmp_path, publish):
    bash = _bash()
    packages = _final_packages(skip="darwin-x64")
    ws, env = _workspace(
        tmp_path, packages, version=FINAL_VERSION, drop_lock_platform="darwin-x64"
    )

    gate = _step(
        publish["tui-installers-complete"], "Every terminal installer is present"
    )
    done = subprocess.run(
        [bash, "-c", gate["run"]], cwd=ws, env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr

    done = _run_attach(bash, ws, env, publish, windows_ok="")
    assert done.returncode == 0, done.stdout + done.stderr
    args = (tmp_path / "gh.args").read_text(encoding="utf-8").splitlines()
    uploaded = sorted(a for a in args if a.startswith("dist/") and "SHA256" not in a)
    assert uploaded == sorted(f"dist/{n}" for n in packages)


@pytest.mark.parametrize("drop", range(4))
def test_a_final_missing_a_locked_package_publishes_nothing(tmp_path, publish, drop):
    bash = _bash()
    packages = _final_packages()
    gone = packages.pop(drop)
    ws, env = _workspace(tmp_path, packages, version=FINAL_VERSION)

    gate = _step(
        publish["tui-installers-complete"], "Every terminal installer is present"
    )
    done = subprocess.run(
        [bash, "-c", gate["run"]], cwd=ws, env=env, capture_output=True, text=True
    )
    assert done.returncode != 0
    assert gone in done.stdout + done.stderr and "::error::" in done.stdout

    done = _run_attach(bash, ws, env, publish, windows_ok="")
    assert done.returncode != 0
    assert not (tmp_path / "gh.args").exists(), "gh ran despite a missing package"


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
