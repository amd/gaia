# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""gaiacpp reaches every GitHub release, a candidate's included.

Two halves: util/gaiacpp_release_assets.py refuses a bundle that is not this
release's, and publish.yml / build_agents.yml stay wired so the build, its
smoke test and the attach step cannot be dropped or made RC-only unnoticed.
"""

import hashlib
import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import check_release_assets as cra  # noqa: E402
import gaiacpp_release_assets as gra  # noqa: E402

WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TAG = "v0.25.4-rc1"
VERSION = "0.25.4-rc.1"
LEGS = (("windows", "x64"), ("linux", "x64"), ("darwin", "arm64"))


@pytest.fixture(scope="module")
def pkg():
    script = REPO_ROOT / "cpp" / "packaging" / "package_lemonade.py"
    spec = importlib.util.spec_from_file_location("package_lemonade", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle(pkg, tmp_path: Path, version: str = VERSION, tag: str = TAG) -> Path:
    """A bundle built the way CI builds it: one leg per platform, then merged."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    pin_files = []
    for os_name, arch in LEGS:
        build = tmp_path / f"build-{os_name}"
        build.mkdir()
        exe = "gaiacpp.exe" if os_name == "windows" else "gaiacpp"
        (build / exe).write_bytes(
            b"\x7fELF " + os_name.encode() + f" gaiacpp {version}\n\0".encode()
        )
        leg = tmp_path / f"leg-{os_name}"
        args = ["--build-dir", str(build), "--os", os_name, "--arch", arch]
        assert pkg.main(args + ["--version", tag, "--out", str(leg)]) == 0
        pin_files.append(str(leg / gra.PINS_NAME))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for os_name, _ in LEGS:
        for archive in (tmp_path / f"leg-{os_name}").glob("gaiacpp-*"):
            (bundle / archive.name).write_bytes(archive.read_bytes())
    assert pkg.main(["--out", str(bundle), "--merge", *pin_files]) == 0
    return bundle


def test_a_complete_bundle_attaches_every_promised_file(pkg, tmp_path):
    out = tmp_path / "release-assets"
    written = gra.collect(_bundle(pkg, tmp_path), TAG, VERSION, out)

    assert written == gra.release_files()
    assert sorted(p.name for p in out.iterdir()) == gra.release_files()
    sums = dict(
        reversed(line.split("  "))
        for line in (out / gra.SUMS_NAME).read_text().splitlines()
    )
    assert sorted(sums) == sorted(set(gra.release_files()) - {gra.SUMS_NAME})
    for name, digest in sums.items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest


def test_the_bare_binary_is_the_one_inside_the_archive(pkg, tmp_path):
    out = tmp_path / "release-assets"
    gra.collect(_bundle(pkg, tmp_path), TAG, VERSION, out)
    with zipfile.ZipFile(out / "gaiacpp-windows-x64.zip") as z:
        assert (out / "gaiacpp-windows-x64.exe").read_bytes() == z.read("gaiacpp.exe")


@pytest.mark.parametrize("archive", sorted(gra.PLATFORMS))
def test_each_missing_archive_fails_and_is_named(pkg, tmp_path, archive):
    bundle = _bundle(pkg, tmp_path)
    (bundle / archive).unlink()
    with pytest.raises(gra.ReleaseAssetError, match=re.escape(archive)):
        gra.collect(bundle, TAG, VERSION, tmp_path / "out")


def test_an_archive_that_is_not_the_pinned_one_is_refused(pkg, tmp_path):
    bundle = _bundle(pkg, tmp_path)
    other = _bundle(pkg, tmp_path / "other", version="9.9.9")
    name = "gaiacpp-linux-x64.tar.gz"
    (bundle / name).write_bytes((other / name).read_bytes())
    with pytest.raises(gra.ReleaseAssetError, match="pins"):
        gra.collect(bundle, TAG, VERSION, tmp_path / "out")


def test_another_tags_bundle_is_refused(pkg, tmp_path):
    bundle = _bundle(pkg, tmp_path, tag="v0.25.3")
    with pytest.raises(gra.ReleaseAssetError, match="v0.25.3"):
        gra.collect(bundle, TAG, VERSION, tmp_path / "out")


def test_a_binary_stamped_with_another_version_is_refused(pkg, tmp_path):
    # 0.25.4 is a prefix of 0.25.4-rc.1: the final's binary must not pass as the RC's.
    bundle = _bundle(pkg, tmp_path, version="0.25.4")
    with pytest.raises(gra.ReleaseAssetError, match="not stamped"):
        gra.collect(bundle, TAG, VERSION, tmp_path / "out")
    rc_bundle = _bundle(pkg, tmp_path / "rc")
    with pytest.raises(gra.ReleaseAssetError, match="not stamped"):
        gra.collect(rc_bundle, TAG, "0.25.4", tmp_path / "out2")


def test_a_bundle_with_no_pin_file_is_refused(pkg, tmp_path):
    bundle = _bundle(pkg, tmp_path)
    (bundle / gra.PINS_NAME).unlink()
    with pytest.raises(gra.ReleaseAssetError, match="release-bundle"):
        gra.collect(bundle, TAG, VERSION, tmp_path / "out")


def test_the_cli_reports_the_problem_and_exits_nonzero(pkg, tmp_path, capsys):
    bundle = _bundle(pkg, tmp_path)
    (bundle / "gaiacpp-darwin-arm64.tar.gz").unlink()
    args = ["--bundle", str(bundle), "--tag", TAG, "--version", VERSION]
    assert gra.main(args + ["--out", str(tmp_path / "out")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("::error::") and "gaiacpp-darwin-arm64.tar.gz" in err


def test_the_release_check_promises_exactly_these_files():
    assert sorted(cra.GAIACPP_FILES) == gra.release_files()


# The workflows


@pytest.fixture(scope="module")
def publish() -> dict:
    return yaml.safe_load((WORKFLOWS / "publish.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def build_agents() -> dict:
    text = (WORKFLOWS / "build_agents.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _step(job: dict, name_part: str) -> int:
    hits = [i for i, s in enumerate(job["steps"]) if name_part in s.get("name", "")]
    assert len(hits) == 1, f"expected one step matching {name_part!r}, got {hits}"
    return hits[0]


def test_publish_builds_gaiacpp_for_finals_and_candidates(publish):
    job = publish["jobs"]["build-gaiacpp"]
    assert job["uses"] == "./.github/workflows/build_agents.yml"
    assert job["with"] == {
        "version": "${{ needs.validate.outputs.npm_version }}",
        "tag": "${{ needs.validate.outputs.tag_name }}",
    }
    # No `if`: a condition here is how the build would become final-only.
    assert "if" not in job


@pytest.mark.parametrize("gate", ["approve-publish", "github-release"])
def test_nothing_publishes_without_gaiacpp(publish, gate):
    job = publish["jobs"][gate]
    assert "build-gaiacpp" in job["needs"]
    assert "needs.build-gaiacpp.result == 'success'" in job["if"]


def test_the_release_attaches_gaiacpp_before_it_is_checked_and_created(publish):
    job = publish["jobs"]["github-release"]
    download = _step(job, "Download the gaiacpp bundle")
    attach = _step(job, "Attach and checksum gaiacpp")
    check = _step(job, "Check the update manifests name only attached files")
    create = _step(job, "Create GitHub Release")
    assert download < attach < check < create
    run = job["steps"][attach]["run"]
    assert "util/gaiacpp_release_assets.py" in run and "--out release-assets" in run
    for step in (job["steps"][download], job["steps"][attach]):
        assert "if" not in step and not step.get("continue-on-error")


def test_build_agents_can_be_called_with_a_version_and_a_tag(build_agents):
    # PyYAML reads the key `on` as True.
    inputs = build_agents[True]["workflow_call"]["inputs"]
    assert inputs["version"]["required"] and inputs["tag"]["required"]


def test_a_release_build_is_never_cancelled_midway(build_agents):
    cancel = build_agents["concurrency"]["cancel-in-progress"]
    assert cancel == "${{ github.event_name == 'pull_request' }}"


def test_the_release_build_stamps_and_smoke_tests_before_packaging(build_agents):
    job = build_agents["jobs"]["build"]
    configure = _step(job, "Configure CMake")
    smoke = _step(job, "Smoke-test gaiacpp")
    package = _step(job, "Package gaiacpp for Lemonade")
    assert configure < smoke < package
    assert "-DGAIACPP_VERSION=" in job["steps"][configure]["run"]
    assert job["steps"][configure]["env"]["GAIACPP_VERSION"] == "${{ inputs.version }}"
    run = job["steps"][smoke]["run"]
    assert "--version" in run and "--mcp" in run
    assert not job["steps"][smoke].get("continue-on-error")
    assert "inputs.tag" in job["steps"][package]["run"]


def test_every_release_platform_has_a_build_leg(build_agents):
    legs = build_agents["jobs"]["build"]["strategy"]["matrix"]["include"]
    built = {f"gaiacpp-{leg['lemonade_os']}-{leg['lemonade_arch']}" for leg in legs}
    promised = {a.split(".")[0] for a in gra.PLATFORMS}
    assert promised <= built


def test_cmake_lets_a_release_set_the_version():
    cmake = (REPO_ROOT / "cpp" / "CMakeLists.txt").read_text(encoding="utf-8")
    assert 'set(GAIACPP_VERSION "${PROJECT_VERSION}" CACHE STRING' in cmake
    assert 'GAIACPP_VERSION="${GAIACPP_VERSION}"' in cmake


def test_the_binary_prints_the_string_the_release_check_looks_for():
    main = (REPO_ROOT / "cpp" / "agents" / "gaia" / "main.cpp").read_text("utf-8")
    assert '"gaiacpp " GAIACPP_VERSION "\\n"' in main


def test_pins_round_trip_as_json(pkg, tmp_path):
    out = tmp_path / "out"
    gra.collect(_bundle(pkg, tmp_path), TAG, VERSION, out)
    pins = json.loads((out / gra.PINS_NAME).read_text())
    assert pins["gaiacpp"]["version"] == TAG
    assert set(pins["checksums"]["github"]["amd/gaia"][TAG]) == set(gra.PLATFORMS)
