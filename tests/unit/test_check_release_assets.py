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


def _mac_update(version: str) -> list[str]:
    ui = f"gaia-agent-ui-{version}"
    return [f"{ui}-arm64.zip", f"{ui}-arm64.zip.blockmap"]


# The frozen gaia the one-line installers fetch, first attached after these.
CORE = [
    "gaia-core-SHA256SUMS.txt",
    "gaia-core-darwin-arm64.tar.gz",
    "gaia-core-linux-x64.tar.gz",
    "gaia-core-win32-x64.tar.gz",
]

# What each release really carried, less its Python and flagship agent files.
# None carried the Mac update zip (#5682) or the frozen gaia, so a complete
# release adds them.
V0_25_2 = _desktop("0.25.2") + _combined("0.25.2") + TUI + FEEDS
V0_25_3 = _desktop("0.25.3") + TUI + FEEDS
V0_25_4_RC1 = _desktop("0.25.4-rc.1") + _combined("0.25.4-rc.1") + TUI + FEEDS
COMPLETE_0_25_2 = V0_25_2 + _mac_update("0.25.2") + CORE
COMPLETE_0_25_4_RC1 = V0_25_4_RC1 + _mac_update("0.25.4-rc.1") + CORE


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
    assert sorted(_missing("v0.25.3", V0_25_3)) == sorted(
        _combined("0.25.3") + _mac_update("0.25.3") + CORE
    )


def test_a_release_without_the_mac_update_zip_is_refused():
    assert _missing("v0.25.2", V0_25_2 + CORE) == _mac_update("0.25.2")


def test_a_release_without_the_frozen_gaia_is_refused():
    # install.sh and install.ps1 fetch it from the release.
    names = V0_25_2 + _mac_update("0.25.2")
    assert sorted(_missing("v0.25.2", names)) == sorted(CORE)


@pytest.mark.parametrize(
    "tag, names", [("v0.25.2", COMPLETE_0_25_2), ("v0.25.4-rc1", COMPLETE_0_25_4_RC1)]
)
def test_a_complete_release_passes(tag, names):
    assert _missing(tag, names) == []


@pytest.mark.parametrize("gone", COMPLETE_0_25_2)
def test_any_single_missing_file_fails(gone):
    assert _missing("v0.25.2", [n for n in COMPLETE_0_25_2 if n != gone]) == [gone]


def test_a_failed_or_empty_upload_does_not_count():
    assets = _assets(COMPLETE_0_25_2)
    assets[0]["state"] = "starter"
    assets[1]["size"] = 0
    promised = cra.promised_assets(parse_tag("v0.25.2"))
    assert sorted(cra.missing_assets(promised, assets)) == sorted(COMPLETE_0_25_2[:2])


def test_a_final_follows_its_flagship_lock(monkeypatch):
    monkeypatch.setattr(
        cra.artifacts, "platform_has_sidecar", lambda p: p != "darwin-x64"
    )
    names = [n for n in COMPLETE_0_25_2 if "darwin-x64" not in n]
    assert _missing("v0.25.2", names) == []


def test_an_rc_needs_every_package_whatever_the_lock_says(monkeypatch):
    monkeypatch.setattr(cra.artifacts, "platform_has_sidecar", lambda p: False)
    gone = "gaia-0.25.4-rc.1-darwin-x64.pkg"
    names = [n for n in COMPLETE_0_25_4_RC1 if n != gone]
    assert _missing("v0.25.4-rc1", names) == [gone]


def test_an_rc_whose_setup_failed_is_only_excused_the_setup():
    setup = "gaia-0.25.4-rc.1-win-x64-setup.exe"
    names = [n for n in COMPLETE_0_25_4_RC1 if n != setup]
    assert _missing("v0.25.4-rc1", names) == [setup]
    assert _missing("v0.25.4-rc1", names, windows_setup=False) == []


# ── update manifests ─────────────────────────────────────────────────────────

# The manifests v0.25.4-rc4 published, verbatim except for shortened digests.
RC4 = "0.25.4-rc.4"
RC4_MAC = f"""version: {RC4}
files:
  - url: gaia-agent-ui-{RC4}-arm64.zip
    sha512: kYETtw==
    size: 145816493
  - url: gaia-agent-ui-{RC4}-arm64.dmg
    sha512: /dJEMh==
    size: 151246487
path: gaia-agent-ui-{RC4}-arm64.zip
sha512: kYETtw==
releaseDate: '2026-11-04T02:31:27.431Z'
"""
RC4_WIN = f"""version: {RC4}
files:
  - url: gaia-agent-ui-{RC4}-x64-setup.exe
    sha512: jLaatD==
    size: 141426679
path: gaia-agent-ui-{RC4}-x64-setup.exe
sha512: jLaatD==
releaseDate: '2026-11-04T02:31:51.561Z'
"""
RC4_LINUX = f"""version: {RC4}
files:
  - url: gaia-agent-ui-{RC4}-x86_64.AppImage
    sha512: LbzPQj==
    size: 158151497
    blockMapSize: 164701
  - url: gaia-agent-ui-{RC4}-amd64.deb
    sha512: i8lPwZ==
    size: 131008428
path: gaia-agent-ui-{RC4}-x86_64.AppImage
sha512: LbzPQj==
releaseDate: '2026-11-04T02:30:54.423Z'
"""
RC4_MANIFESTS = {
    "latest-mac.yml": RC4_MAC,
    "latest.yml": RC4_WIN,
    "latest-linux.yml": RC4_LINUX,
}
# Name -> size of the desktop files rc4 really carried.
RC4_DESKTOP = {
    f"gaia-agent-ui-{RC4}-amd64.deb": 131008428,
    f"gaia-agent-ui-{RC4}-arm64.dmg": 151246487,
    f"gaia-agent-ui-{RC4}-arm64.dmg.blockmap": 159528,
    f"gaia-agent-ui-{RC4}-x64-setup.exe": 141426679,
    f"gaia-agent-ui-{RC4}-x64-setup.exe.blockmap": 149346,
    f"gaia-agent-ui-{RC4}-x86_64.AppImage": 158151497,
}
RC4_ZIP = {
    f"gaia-agent-ui-{RC4}-arm64.zip": 145816493,
    f"gaia-agent-ui-{RC4}-arm64.zip.blockmap": 150000,
}


def test_rc4_is_refused_for_the_mac_zip_its_manifest_names():
    problems = cra.manifest_problems(RC4_MANIFESTS, RC4_DESKTOP)
    assert problems == [
        f"latest-mac.yml names gaia-agent-ui-{RC4}-arm64.zip, which is not attached",
        f"latest-mac.yml names gaia-agent-ui-{RC4}-arm64.zip.blockmap, "
        "which is not attached",
    ]


def test_rc4_with_its_mac_zip_passes():
    assert cra.manifest_problems(RC4_MANIFESTS, {**RC4_DESKTOP, **RC4_ZIP}) == []


def test_an_embedded_blockmap_needs_no_file():
    """The AppImage carries its blockmap (blockMapSize); the .deb is never path."""
    assert cra.manifest_problems({"latest-linux.yml": RC4_LINUX}, RC4_DESKTOP) == []


def test_a_size_that_differs_from_the_manifest_fails():
    sizes = {**RC4_DESKTOP, f"gaia-agent-ui-{RC4}-x64-setup.exe": 141426680}
    (problem,) = cra.manifest_problems({"latest.yml": RC4_WIN}, sizes)
    assert "141426679 bytes" in problem and "141426680" in problem


def test_a_digest_that_differs_from_the_manifest_fails():
    digests = {f"gaia-agent-ui-{RC4}-x64-setup.exe": "re-signed=="}
    (problem,) = cra.manifest_problems(
        {"latest.yml": RC4_WIN}, RC4_DESKTOP, digests.get
    )
    assert "sha512" in problem and "x64-setup.exe" in problem


@pytest.mark.parametrize(
    "text", ["version: 1\n", "files: []\npath: x\n", "{not: [yaml\n", "- a\n"]
)
def test_a_manifest_the_updater_cannot_read_fails(text):
    (problem,) = cra.manifest_problems({"latest.yml": text}, RC4_DESKTOP)
    assert problem.startswith("latest.yml ")


def _write_dir(tmp_path: Path, files: dict[str, bytes]) -> Path:
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    return tmp_path


def _built(installer: bytes = b"installer") -> dict[str, bytes]:
    digest = cra.base64.b64encode(cra.hashlib.sha512(installer).digest()).decode()
    manifest = (
        "version: 1.0.0\nfiles:\n"
        f"  - url: app.zip\n    sha512: {digest}\n    size: {len(installer)}\n"
        f"path: app.zip\nsha512: {digest}\n"
    )
    return {
        "latest-mac.yml": manifest.encode(),
        "app.zip": installer,
        "app.zip.blockmap": b"map",
    }


def test_the_dir_mode_passes_files_as_built(tmp_path, capsys):
    assert cra.main(["--dir", str(_write_dir(tmp_path, _built()))]) == 0
    assert capsys.readouterr().out.startswith("OK: ")


def test_the_dir_mode_fails_a_file_changed_after_the_build(tmp_path, capsys):
    files = {**_built(), "app.zip": b"INSTALLER"}  # same size, other bytes
    assert cra.main(["--dir", str(_write_dir(tmp_path, files))]) == 1
    out = capsys.readouterr().out
    assert out.startswith("::error::") and "sha512 for app.zip" in out


def test_the_dir_mode_fails_a_dropped_installer(tmp_path, capsys):
    files = {k: v for k, v in _built().items() if k != "app.zip"}
    assert cra.main(["--dir", str(_write_dir(tmp_path, files))]) == 1
    assert "names app.zip, which is not attached" in capsys.readouterr().out


def test_the_dir_mode_fails_with_no_manifest(tmp_path, capsys):
    assert cra.main(["--dir", str(tmp_path)]) == 1
    assert "no latest*.yml" in capsys.readouterr().out


def test_the_dir_mode_takes_no_release(tmp_path):
    with pytest.raises(SystemExit):
        cra.main(["--dir", str(tmp_path), "--tag", "v0.25.3"])


def _run(
    monkeypatch,
    capsys,
    names: list[str],
    *argv: str,
    manifests: dict[str, str] | None = None,
) -> tuple[int, str]:
    calls = []
    manifests = manifests or {
        f: "version: 1\nfiles:\n  - url: a.bin\n    size: 1024\npath: a.bin\n"
        for f in FEEDS
    }
    names = [*names, "a.bin", "a.bin.blockmap"]

    def gh(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "release", "download"]:
            out = Path(cmd[cmd.index("--dir") + 1])
            for i, arg in enumerate(cmd):
                if arg == "--pattern":
                    (out / cmd[i + 1]).write_text(manifests[cmd[i + 1]])
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
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
    assert out.startswith("::error::release v0.25.3 is missing 12 of ")
    assert "gaia-0.25.3-win-x64-setup.exe" in out
    assert "'Attach terminal installers'" in out and "'GitHub Release'" in out


def test_the_cli_passes_a_complete_release(monkeypatch, capsys):
    code, out = _run(monkeypatch, capsys, COMPLETE_0_25_2, "--tag", "v0.25.2")
    assert code == 0 and "v0.25.2" in out


def test_the_cli_fails_a_manifest_naming_an_unattached_file(monkeypatch, capsys):
    manifests = {**dict.fromkeys(FEEDS, RC4_WIN), "latest-mac.yml": RC4_MAC}
    names = COMPLETE_0_25_2 + list(RC4_DESKTOP)
    code, out = _run(
        monkeypatch, capsys, names, "--tag", "v0.25.2", manifests=manifests
    )
    assert code == 1
    assert out.startswith("::error::release v0.25.2's desktop update manifests")
    assert f"latest-mac.yml names gaia-agent-ui-{RC4}-arm64.zip" in out


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


def test_a_manifest_download_that_hangs_fails_with_a_clear_error(monkeypatch):
    seen = {}

    def hang(cmd, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(cra.subprocess, "run", hang)
    with pytest.raises(SystemExit, match="did not finish within 120s") as exc:
        cra.fetch_manifests("amd/gaia", "v0.25.3", ["latest.yml"])
    assert seen["timeout"] == cra.GH_TIMEOUT == 120
    assert "latest.yml" in str(exc.value) and "v0.25.3" in str(exc.value)


def test_a_release_read_that_hangs_fails_with_a_clear_error(monkeypatch):
    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(cra.subprocess, "run", hang)
    with pytest.raises(SystemExit, match="v0.25.3 of amd/gaia did not finish"):
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
    # The check runs inside util/gh_retry.py, which waits out a spent API budget.
    assert args[:4] == ["util/gh_retry.py", "--max-wait-seconds", "3600", "--"]
    assert args[4:8] == [
        "python3",
        "util/check_release_assets.py",
        "--repo",
        "amd/gaia",
    ]


def test_the_binaries_match_the_ones_the_release_checksums(jobs):
    (step,) = [
        s
        for s in jobs["github-release"]["steps"]
        if s.get("name") == "Checksum TUI binaries"
    ]
    for name in cra.TUI_FILES:
        assert name in step["run"]
