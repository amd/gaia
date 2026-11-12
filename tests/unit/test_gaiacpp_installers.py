# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every terminal installer ships gaiacpp beside gaia-tui, final and RC alike.

The terminal UI runs gaiacpp when it sits beside it and gaia-agent otherwise,
so an installer that drops gaiacpp silently moves its users back to the Python
agent. Two halves: installer/tui/fetch_gaiacpp.py refuses a gaiacpp that is
not this release's, and the workflows and installer scripts stay wired so it
reaches every payload. None of the release workflows can be dry-run here, so
their wiring is asserted instead.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
INSTALLER_TUI = REPO_ROOT / "installer" / "tui"
sys.path.insert(0, str(INSTALLER_TUI))
sys.path.insert(0, str(REPO_ROOT / "util"))

import artifacts  # noqa: E402
import fetch_gaiacpp as fg  # noqa: E402
import gaiacpp_release_assets as gra  # noqa: E402

TAG = "v0.25.4-rc1"
VERSION = "0.25.4-rc.1"
LEGS = (("windows", "x64"), ("linux", "x64"), ("darwin", "arm64"))
VALID_SOURCES = {"artifact", "release", "stub"}


# ── fetch_gaiacpp ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def pkg():
    script = REPO_ROOT / "cpp" / "packaging" / "package_lemonade.py"
    spec = importlib.util.spec_from_file_location("package_lemonade", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle(pkg, tmp_path: Path, version: str = VERSION) -> Path:
    """A bundle the way build_agents.yml builds one: a leg per platform, merged."""
    pins = []
    for os_name, arch in LEGS:
        build = tmp_path / f"build-{os_name}"
        build.mkdir(parents=True)
        exe = "gaiacpp.exe" if os_name == "windows" else "gaiacpp"
        (build / exe).write_bytes(
            f"\x7fELF {os_name} gaiacpp {version}\n\0".encode("latin-1")
        )
        leg = tmp_path / f"leg-{os_name}"
        args = ["--build-dir", str(build), "--os", os_name, "--arch", arch]
        assert pkg.main(args + ["--version", TAG, "--out", str(leg)]) == 0
        pins.append(str(leg / gra.PINS_NAME))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for os_name, _ in LEGS:
        for archive in (tmp_path / f"leg-{os_name}").glob("gaiacpp-*"):
            (bundle / archive.name).write_bytes(archive.read_bytes())
    assert pkg.main(["--out", str(bundle), "--merge", *pins]) == 0
    return bundle


@pytest.fixture
def verified(pkg, tmp_path) -> Path:
    out = tmp_path / "gaiacpp"
    fg.from_bundle(_bundle(pkg, tmp_path / "build"), TAG, VERSION, out)
    return out


def test_every_gaiacpp_a_release_attaches_has_a_terminal_platform():
    attached = {bare for _, bare in gra.PLATFORMS.values()}
    assert set(fg.PLATFORM_BINARIES.values()) == attached
    assert set(fg.PLATFORM_BINARIES) <= set(fg.TERMINAL_PLATFORMS)


def test_every_platform_with_gaiacpp_gets_an_installer():
    # A gaiacpp for a platform no installer is built for would never ship.
    assert set(fg.PLATFORM_BINARIES) <= set(artifacts.ARTIFACTS)


def test_the_bundle_stages_each_platforms_own_binary(verified, tmp_path):
    for platform, bare in fg.PLATFORM_BINARIES.items():
        dest = tmp_path / "stage" / platform
        target = fg.stage(verified, platform, VERSION, dest)
        assert target.name == ("gaiacpp.exe" if platform == "win-x64" else "gaiacpp")
        assert target.read_bytes() == (verified / bare).read_bytes()


def test_a_platform_with_no_build_is_exit_3_not_an_error(verified, tmp_path):
    assert fg.main(["has", "--platform", "darwin-x64"]) == 3
    assert fg.main(["has", "--platform", "darwin-arm64"]) == 0
    argv = ["stage", "--dir", str(verified), "--platform", "darwin-x64"]
    argv += ["--version", VERSION, "--dest", str(tmp_path / "s")]
    assert fg.main(argv) == fg.EXIT_NO_GAIACPP_FOR_PLATFORM
    assert not (tmp_path / "s").exists()


def test_a_binary_changed_after_verification_is_not_staged(verified, tmp_path):
    (verified / "gaiacpp-linux-x64").write_bytes(b"gaiacpp " + VERSION.encode() + b"\n")
    with pytest.raises(SystemExit, match="SHA-256 mismatch"):
        fg.stage(verified, "linux-x64", VERSION, tmp_path / "s")
    assert not (tmp_path / "s" / "gaiacpp").exists()


def test_another_releases_binary_is_not_staged(verified, tmp_path):
    with pytest.raises(SystemExit, match="not stamped 'gaiacpp 0.25.5'"):
        fg.stage(verified, "win-x64", "0.25.5", tmp_path / "s")


def test_the_bundle_must_be_the_named_releases(pkg, tmp_path):
    bundle = _bundle(pkg, tmp_path / "build")
    with pytest.raises(SystemExit, match="is not v0.25.4-rc2's"):
        fg.from_bundle(bundle, "v0.25.4-rc2", "0.25.4-rc.2", tmp_path / "out")
    with pytest.raises(SystemExit, match="these installers are version 0.25.4"):
        fg.from_bundle(bundle, TAG, "0.25.4", tmp_path / "out")


@pytest.mark.parametrize(
    "listing,match",
    [
        ("not a checksum line\n", "is not '<sha256>  <name>'"),
        (f"{'a' * 64}  x\n{'b' * 64}  x\n", "lists x twice"),
    ],
)
def test_an_ambiguous_checksum_list_is_refused(tmp_path, listing, match):
    path = tmp_path / gra.SUMS_NAME
    path.write_text(listing, encoding="utf-8")
    with pytest.raises(SystemExit, match=match):
        fg.read_sums(path)


def _release_assets(version: str = VERSION) -> dict[str, bytes]:
    blobs = {
        bare: f"binary {bare} gaiacpp {version}\n".encode()
        for bare in fg.PLATFORM_BINARIES.values()
    }
    blobs[gra.SUMS_NAME] = "".join(
        f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in sorted(blobs.items())
    ).encode()
    return blobs


def _serve(monkeypatch, assets: dict[str, bytes]) -> list[str]:
    fetched: list[str] = []

    def download(url):
        fetched.append(url)
        name = url.rsplit("/", 1)[1]
        if name not in assets or f"/download/{TAG}/" not in url:
            raise SystemExit(
                f"{url} is 404: the release is unpublished or lacks that asset."
            )
        return assets[name]

    monkeypatch.setattr(fg, "_download", download)
    return fetched


def test_a_published_release_stages_like_a_bundle(monkeypatch, tmp_path):
    _serve(monkeypatch, _release_assets())
    out = tmp_path / "gaiacpp"
    written = fg.from_release("amd/gaia", TAG, VERSION, out)
    assert written == sorted([gra.SUMS_NAME, *fg.PLATFORM_BINARIES.values()])
    target = fg.stage(out, "darwin-arm64", VERSION, tmp_path / "s")
    assert target.read_bytes() == _release_assets()["gaiacpp-darwin-arm64"]


def test_a_release_without_gaiacpp_says_what_to_do(monkeypatch, tmp_path):
    _serve(monkeypatch, {})
    with pytest.raises(SystemExit, match="predates gaiacpp") as excinfo:
        fg.from_release("amd/gaia", TAG, VERSION, tmp_path / "out")
    assert "publish.yml" in str(excinfo.value)


def test_a_tampered_release_binary_is_refused_and_deleted(monkeypatch, tmp_path):
    assets = _release_assets()
    assets["gaiacpp-linux-x64"] = b"something else gaiacpp 0.25.4-rc.1\n"
    _serve(monkeypatch, assets)
    out = tmp_path / "out"
    with pytest.raises(SystemExit, match="SHA-256 mismatch for gaiacpp-linux-x64"):
        fg.from_release("amd/gaia", TAG, VERSION, out)
    assert not (out / "gaiacpp-linux-x64").exists()


def test_a_release_listing_no_line_for_a_binary_is_refused(monkeypatch, tmp_path):
    assets = _release_assets()
    assets[gra.SUMS_NAME] = b"".join(
        line + b"\n"
        for line in assets[gra.SUMS_NAME].splitlines()
        if not line.endswith(b"gaiacpp-windows-x64.exe")
    )
    _serve(monkeypatch, assets)
    with pytest.raises(SystemExit, match="no line for gaiacpp-windows-x64.exe"):
        fg.from_release("amd/gaia", TAG, VERSION, tmp_path / "out")


# ── The workflows ────────────────────────────────────────────────────────────


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _calls(reusable: str) -> list[tuple[str, str, dict]]:
    """(workflow, job, with) for every job that calls `reusable`."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job_id, job in (_workflow(path.name).get("jobs") or {}).items():
            if job.get("uses") == f"./.github/workflows/{reusable}":
                found.append((path.name, job_id, job.get("with") or {}))
    return found


def _step(job: dict, name: str) -> dict:
    (step,) = [s for s in job["steps"] if s.get("name") == name]
    return step


@pytest.mark.parametrize("reusable", ["tui_installers.yml", "windows_setup.yml"])
def test_every_caller_names_a_gaiacpp_source(reusable):
    calls = _calls(reusable)
    assert calls, f"nothing calls {reusable}"
    for workflow, job_id, with_ in calls:
        assert with_.get("gaiacpp") in VALID_SOURCES, f"{workflow}:{job_id}"


def test_the_inputs_have_no_default_source():
    # A default would let a new caller ship packages without gaiacpp unasked.
    for name in ("tui_installers.yml", "windows_setup.yml"):
        inputs = _workflow(name)[True]["workflow_call"]["inputs"]
        assert inputs["gaiacpp"]["default"] == "", name


def test_a_publishing_workflow_never_ships_a_stand_in():
    for workflow in ("publish.yml", "release_components.yml"):
        for reusable in ("tui_installers.yml", "windows_setup.yml"):
            for wf, job_id, with_ in _calls(reusable):
                if wf == workflow:
                    assert with_["gaiacpp"] != "stub", f"{wf}:{job_id}"


def test_each_windows_setup_takes_the_source_its_terminal_run_verified():
    tui = {
        (wf, w.get("artifact_prefix", "")): w["gaiacpp"]
        for wf, _, w in _calls("tui_installers.yml")
    }
    for wf, job_id, with_ in _calls("windows_setup.yml"):
        assert tui[(wf, with_.get("artifact_prefix", ""))] == with_["gaiacpp"], job_id


def test_every_tag_packages_this_runs_gaiacpp():
    jobs = _workflow("publish.yml")["jobs"]
    tui = jobs["tui-installers"]
    assert "build-gaiacpp" in tui["needs"]
    assert "needs.build-gaiacpp.result == 'success'" in tui["if"]
    # Neither final-only nor RC-only.
    assert "is_rc" not in tui["if"].replace(
        "(needs.validate.outputs.is_rc != 'true' || needs.rc-flagship.result == 'success')",
        "",
    )
    assert tui["with"]["gaiacpp"] == "artifact"
    assert tui["with"]["gaiacpp_tag"] == "${{ needs.validate.outputs.tag_name }}"
    assert jobs["windows-setup"]["with"]["gaiacpp"] == "artifact"
    # The bundle tui_installers.yml reads is the one build-gaiacpp builds.
    assert jobs["build-gaiacpp"]["with"]["tag"] == tui["with"]["gaiacpp_tag"]
    assert "if" not in jobs["build-gaiacpp"]


def test_a_by_hand_rebuild_takes_the_published_releases_gaiacpp():
    jobs = _workflow("release_components.yml")["jobs"]
    assert jobs["tui"]["with"]["gaiacpp"] == "release"
    assert (
        jobs["tui"]["with"]["gaiacpp_tag"]
        == "v${{ needs.version.outputs.tui_version }}"
    )
    assert jobs["windows-setup"]["with"]["gaiacpp"] == "release"


@pytest.fixture(scope="module")
def tui_jobs() -> dict:
    return _workflow("tui_installers.yml")["jobs"]


def test_gaiacpp_is_verified_before_any_package_is_built(tui_jobs):
    job = tui_jobs["gaiacpp"]
    (download,) = [
        s
        for s in job["steps"]
        if s.get("uses", "").startswith("actions/download-artifact")
    ]
    assert download["with"]["name"] == "gaia-cpp-agents-${{ inputs.gaiacpp_tag }}"
    run = _step(job, "Verify gaiacpp")["run"]
    assert "fetch_gaiacpp.py bundle --bundle gaiacpp-bundle/lemonade" in run
    assert "fetch_gaiacpp.py release" in run
    # Anything but the three named sources stops the run.
    assert re.search(r"\*\)\n\s+echo \"::error::gaiacpp must be", run)
    assert "gaiacpp" in tui_jobs["tui-installers"]["needs"]
    (upload,) = [
        s
        for s in job["steps"]
        if s.get("uses", "").startswith("actions/upload-artifact")
    ]
    assert upload["with"]["if-no-files-found"] == "error"


def test_each_payload_stages_gaiacpp_where_one_is_built(tui_jobs):
    run = _step(tui_jobs["tui-installers"], "Stage payloads")["run"]
    assert "fetch_gaiacpp.py has --platform" in run
    assert "fetch_gaiacpp.py stage --dir gaiacpp" in run
    # Exit 3 is the one tolerated outcome; any other failure fails the leg.
    assert '[ "${rc}" -eq 3 ]' in run and 'exit "${rc}"' in run


def test_each_smoke_test_runs_the_installed_gaiacpp(tui_jobs):
    smoke = tui_jobs["tui-installers-smoke"]
    mac = _step(smoke, "macOS — install, verify, uninstall")["run"]
    assert '[ "${got}" = "gaiacpp ${VERSION}" ]' in mac
    assert "command -v gaiacpp" in mac
    assert "gaiacpp_built darwin-x64" in mac
    assert "/usr/local/bin/gaiacpp /usr/local/bin/gaia\n" in mac
    assert "gaiacpp survived removal" in mac
    linux = _step(smoke, "Linux — install, verify, uninstall in clean containers")[
        "run"
    ]
    assert (
        'expect "gaiacpp --version" "$(gaiacpp --version)" "gaiacpp ${VERSION}"'
        in linux
    )
    # Both the .deb and the .rpm run it, and both remove it.
    assert len(re.findall(r"^\s*native_agent$", linux, re.M)) == 2
    # Twice after removal; the third is native_agent's own "none built" branch.
    assert len(re.findall(r"^\s*absent /usr/bin/gaiacpp$", linux, re.M)) == 3


def test_the_windows_setup_stages_and_smoke_tests_gaiacpp():
    jobs = _workflow("windows_setup.yml")["jobs"]
    run = _step(jobs["build"], "Stage the terminal payload")["run"]
    assert "fetch_gaiacpp.py stage --dir gaiacpp" in run and "--platform win-x64" in run
    assert "gaiacpp must be artifact, release or stub" in run
    smoke = _step(jobs["smoke"], "Install, verify, uninstall")["run"]
    assert "-StubGaiacpp:($env:GAIACPP -eq 'stub')" in smoke


# ── The installers ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("script", ["linux/build-packages.sh", "macos/build-pkg.sh"])
def test_the_unix_packages_carry_gaiacpp_from_the_payload(script):
    text = (INSTALLER_TUI / script).read_text(encoding="utf-8")
    assert 'readonly NATIVE_AGENT="gaiacpp"' in text
    assert 'SHIPPED_BINARIES+=("$NATIVE_AGENT")' in text
    assert 'for binary in "${SHIPPED_BINARIES[@]}"; do' in text


def _nsi() -> str:
    nsi = INSTALLER_TUI / "nsis" / "gaia-setup.nsi"
    return nsi.read_text(encoding="utf-8").replace("\r\n", "\n")


def _section(nsi: str, header: str, end: str) -> str:
    m = re.search(rf"^{re.escape(header)}\n(.*?)^{end}", nsi, re.M | re.S)
    assert m, f"gaia-setup.nsi has no {header}"
    return m.group(1)


def test_the_windows_setup_installs_checks_and_removes_gaiacpp():
    nsi = _nsi()
    assert re.search(r'^!define NATIVE_AGENT_EXE\s+"gaiacpp.exe"', nsi, re.M)
    main = _section(nsi, 'Section "GAIA Terminal UI (terminal)" SecMain', "SectionEnd")
    assert 'File "${PAYLOAD_DIR}\\${NATIVE_AGENT_EXE}"' in main
    preflight = _section(nsi, "Function Preflight", "FunctionEnd")
    assert 'StrCpy $R8 "${NATIVE_AGENT_EXE}"\n    Call AbortIfRunning' in preflight
    uninstall = _section(nsi, 'Section "Uninstall"', "SectionEnd")
    assert 'Delete "$INSTDIR\\${NATIVE_AGENT_EXE}"' in uninstall
    build = (INSTALLER_TUI / "nsis" / "build-setup.sh").read_text(encoding="utf-8")
    assert '"${PAYLOAD}/gaiacpp.exe"' in build


def test_the_windows_smoke_test_runs_holds_and_removes_gaiacpp():
    smoke = (INSTALLER_TUI / "nsis" / "smoke-test.ps1").read_text(encoding="utf-8")
    assert "'gaiacpp.exe', 'gaia.exe'" in smoke
    assert '-cne "gaiacpp $Version"' in smoke
    assert "re-install over an in-use gaiacpp should exit 2" in smoke
    assert '"$tuiDir\\gaiacpp.exe")' in smoke  # waited on before the upgrade


def test_the_one_line_installers_fetch_gaiacpp_from_the_tuis_release():
    sh = (REPO_ROOT / "installer" / "scripts" / "install.sh").read_text(
        encoding="utf-8"
    )
    ps1 = (
        (REPO_ROOT / "installer" / "scripts" / "install.ps1")
        .read_text(encoding="utf-8")
        .replace("\r\n", "\n")
    )
    assert f'GAIACPP_SUMS_FILE="{gra.SUMS_NAME}"' in sh
    assert f'$GAIACPP_SUMS_FILE = "{gra.SUMS_NAME}"' in ps1
    for platform in ("linux-x64", "darwin-arm64"):
        assert f"'{fg.PLATFORM_BINARIES[platform]}\\n'" in sh
    assert f'$filename = "{fg.PLATFORM_BINARIES["win-x64"]}"' in ps1
    # In main, after the agent, from the release gaia-tui was installed from.
    assert sh.index("\n    install_flagship_agent\n") < sh.index(
        "\n    install_gaiacpp\n"
    )
    assert ps1.index("Install-FlagshipAgent -Pin $pin") < ps1.index(
        "\n    Install-Gaiacpp\n"
    )
