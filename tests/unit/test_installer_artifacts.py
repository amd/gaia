# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The installer filename table must keep agreeing with the scripts that emit them.

`installer/tui/artifacts.py` is what the publish step and the smoke tests read.
The three build scripts spell their own output names in shell. Nothing else
connects the two, and the publisher uploads serially into immutable paths -- so a
rename that reaches only one side half-publishes a version that can never be
completed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER_TUI = REPO_ROOT / "installer" / "tui"
sys.path.insert(0, str(INSTALLER_TUI))

import artifacts  # noqa: E402
import fetch_sidecar  # noqa: E402

VERSION = "1.4.0"


def _assign(script: Path, name: str) -> str:
    """The right-hand side of `[readonly ]NAME="..."` in a shell script."""
    text = script.read_text(encoding="utf-8")
    match = re.search(rf'^(?:readonly )?{name}="([^"]+)"', text, re.MULTILINE)
    assert match, f"{script.name} no longer assigns {name}"
    return match.group(1)


def _expand(raw: str, **values: str) -> str:
    """Resolve `${VAR}` against *values*; a leading `${OUT_DIR}/` is dropped."""
    raw = raw.replace("${OUT_DIR}/", "")
    for key, value in values.items():
        raw = raw.replace(f"${{{key}}}", value)
    assert "${" not in raw, f"unresolved shell variable in {raw!r}"
    return raw


def _names(platform: str) -> set[str]:
    return {name for name, _ in artifacts.artifacts_for(platform, VERSION)}


def test_windows_setup_name_matches_build_script():
    raw = _assign(INSTALLER_TUI / "nsis" / "build-setup.sh", "OUTFILE_NAME")
    assert _expand(raw, VERSION=VERSION) in _names("win-x64")


@pytest.mark.parametrize("arch", ["arm64", "x64"])
def test_macos_pkg_name_matches_build_script(arch):
    raw = _assign(INSTALLER_TUI / "macos" / "build-pkg.sh", "PRODUCT_PKG")
    assert _expand(raw, VERSION=VERSION, ARCH=arch) in _names(f"darwin-{arch}")


@pytest.mark.parametrize("var", ["DEB_PATH", "RPM_PATH"])
def test_linux_package_names_match_build_script(var):
    script = INSTALLER_TUI / "linux" / "build-packages.sh"
    pkg_name = _assign(script, "PKG_NAME")
    raw = _assign(script, var)
    assert _expand(raw, VERSION=VERSION, PKG_NAME=pkg_name) in _names("linux-x64")


def test_website_download_map_matches_the_table():
    # The site builds every download link from its own copy of these names. It
    # cannot import Python, so this is what keeps the fourth copy honest.
    ts = (REPO_ROOT / "website" / "src" / "scripts" / "download-target.ts").read_text(
        encoding="utf-8"
    )
    block = re.search(
        r"const INSTALLER_FILENAMES[^{]*\{(.*?)\n\};", ts, re.DOTALL
    ).group(1)
    site = {
        key: tmpl.replace("${v}", "{version}")
        for key, tmpl in re.findall(r"'([^']+)':\s*\(v\)\s*=>\s*`([^`]+)`", block)
    }
    table = {
        key: template
        for entries in artifacts.ARTIFACTS.values()
        for template, key in entries
    }
    assert site == table


def test_every_installer_platform_has_a_sidecar_lane():
    # An installer for a platform fetch_sidecar.py cannot serve would ship the
    # terminal hub with no agent behind it.
    assert set(artifacts.ARTIFACTS) <= set(fetch_sidecar.PLATFORM_KEYS)


def test_publish_keys_are_unique_and_not_raw_binary_keys():
    keys = [key for entries in artifacts.ARTIFACTS.values() for _, key in entries]
    assert len(keys) == len(set(keys))
    # `_filename_matches_platform` in src/gaia/hub/installer.py is an endswith on
    # the platform key, so reusing a raw-binary key would make a setup a
    # candidate wherever the raw binary is expected.
    assert not set(keys) & set(artifacts.ARTIFACTS)


def test_publish_args_refuses_when_a_named_artifact_is_absent(tmp_path, capsys):
    (tmp_path / next(iter(_names("win-x64")))).write_bytes(b"setup")
    with pytest.raises(SystemExit) as excinfo:
        artifacts.main_argv(
            ["publish-args", "--version", VERSION, "--dir", str(tmp_path)]
        )
    message = str(excinfo.value)
    assert "not on disk" in message
    assert "Nothing has been published" in message


def test_publish_args_emits_path_equals_key_for_every_built_artifact(tmp_path, capsys):
    for platform in artifacts.ARTIFACTS:
        for name, _ in artifacts.artifacts_for(platform, VERSION):
            (tmp_path / name).write_bytes(b"x")
    assert (
        artifacts.main_argv(
            ["publish-args", "--version", VERSION, "--dir", str(tmp_path)]
        )
        == 0
    )
    emitted = dict(
        line.split("=", 1) for line in capsys.readouterr().out.strip().splitlines()
    )
    expected = {
        (tmp_path / name).as_posix(): key
        for platform in artifacts.ARTIFACTS
        for name, key in artifacts.artifacts_for(platform, VERSION)
    }
    assert emitted == expected


def test_publish_args_skips_a_platform_the_lock_has_no_sidecar_for(
    tmp_path, monkeypatch, capsys
):
    # release_agent_gaia.yml DROPS a platform whose best-effort sidecar build was
    # skipped. That means "no installer for this platform", not "release broken".
    monkeypatch.setattr(
        artifacts, "platform_has_sidecar", lambda p: p != "darwin-x64", raising=True
    )
    for platform in artifacts.ARTIFACTS:
        if platform == "darwin-x64":
            continue
        for name, _ in artifacts.artifacts_for(platform, VERSION):
            (tmp_path / name).write_bytes(b"x")
    assert (
        artifacts.main_argv(
            ["publish-args", "--version", VERSION, "--dir", str(tmp_path)]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "darwin-x64" not in captured.out
    assert "skipping darwin-x64" in captured.err


def test_names_refuses_a_filter_that_matches_nothing():
    with pytest.raises(SystemExit):
        artifacts.main_argv(
            ["names", "--version", VERSION, "--platform", "win-x64", "--ext", "deb"]
        )


def _lock(tmp_path: Path, platforms: dict) -> Path:
    path = tmp_path / "binaries.lock.json"
    path.write_text(
        json.dumps(
            {
                "components": {
                    "sidecar": {"componentVersion": "1.2.3", "platforms": platforms}
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_absent_platform_is_no_installer_not_a_hard_stop(tmp_path):
    # Distinct outcomes, deliberately: absent means "build no installer here",
    # a placeholder digest means "stop, this cannot be verified".
    lock = _lock(tmp_path, {"win32-x64": {"sha256": "a" * 64}})
    sidecar = json.loads(lock.read_text())["components"]["sidecar"]
    with pytest.raises(fetch_sidecar.NoSidecarForPlatform):
        fetch_sidecar._expected_sha256(sidecar, "darwin-x64", lock)


def test_placeholder_digest_is_a_hard_stop(tmp_path):
    lock = _lock(tmp_path, {"darwin-x64": {"sha256": "PENDING-replace-with-real"}})
    sidecar = json.loads(lock.read_text())["components"]["sidecar"]
    with pytest.raises(SystemExit) as excinfo:
        fetch_sidecar._expected_sha256(sidecar, "darwin-x64", lock)
    assert not isinstance(excinfo.value, fetch_sidecar.NoSidecarForPlatform)
    assert "cannot be verified" in str(excinfo.value)
