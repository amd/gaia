# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for ``gaia.hub.packager`` (gaia agent pack).

The Python build frontend (``python -m build``) is never invoked for real: a
fake ``runner`` writes a placeholder wheel into the output dir so we can assert
the produced path, checksum, and the fail-loudly error paths without a toolchain
or network.
"""

import hashlib

import pytest

from gaia.hub import packager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PYPROJECT = """\
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "gaia-agent-demo-agent"
version = "0.1.0"
description = "Demo agent"
license = { text = "MIT" }
requires-python = ">=3.10"
dependencies = ["amd-gaia>=0.20.0"]

[project.entry-points."gaia.agent"]
demo-agent = "gaia_agent_demo_agent:build_registration"
"""

_MANIFEST = """\
id: demo-agent
name: Demo Agent
version: 0.1.0
description: "Demo agent for packaging tests"
author: AMD
license: MIT

language: python
min_gaia_version: "0.20.0"

python:
  entry_module: gaia_agent_demo_agent
  entry_class: DemoAgent
  dependencies:
    - "amd-gaia>=0.20.0"
"""

_WHEEL_BYTES = b"PK\x03\x04 fake wheel payload for checksum test"
_EXPECTED_WHEEL = "gaia_agent_demo_agent-0.1.0-py3-none-any.whl"


def _make_package(tmp_path, *, language="python", with_pyproject=True):
    pkg = tmp_path / "demo-agent"
    code = pkg / "gaia_agent_demo_agent"
    code.mkdir(parents=True)
    (code / "__init__.py").write_text("", encoding="utf-8")
    manifest = _MANIFEST
    if language != "python":
        manifest = manifest.replace("language: python", "language: cpp")
        manifest += "\ncpp:\n  binaries:\n    linux-x64: build/demo-agent\n"
    (pkg / "gaia-agent.yaml").write_text(manifest, encoding="utf-8")
    if with_pyproject:
        (pkg / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    return pkg


def _wheel_writing_runner(filename=_EXPECTED_WHEEL, payload=_WHEEL_BYTES):
    """Return a runner that drops *filename* into the build's --outdir."""

    def _runner(cmd, cwd):
        outdir = cmd[cmd.index("--outdir") + 1]
        from pathlib import Path

        Path(outdir).mkdir(parents=True, exist_ok=True)
        (Path(outdir) / filename).write_bytes(payload)
        return 0, "Successfully built " + filename

    return _runner


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_pack_produces_wheel_path_and_checksum(tmp_path):
    pkg = _make_package(tmp_path)
    result = packager.pack(pkg, runner=_wheel_writing_runner())

    assert result.wheel_path.name == _EXPECTED_WHEEL
    assert result.wheel_path.exists()
    assert result.agent_id == "demo-agent"
    assert result.version == "0.1.0"
    assert result.dist_name == "gaia-agent-demo-agent"
    assert result.size_bytes == len(_WHEEL_BYTES)
    assert result.sha256 == hashlib.sha256(_WHEEL_BYTES).hexdigest()


def test_pack_defaults_to_package_dist_dir(tmp_path):
    pkg = _make_package(tmp_path)
    result = packager.pack(pkg, runner=_wheel_writing_runner())
    assert result.wheel_path.parent == (pkg / "dist")


def test_pack_honours_output_dir(tmp_path):
    pkg = _make_package(tmp_path)
    out = tmp_path / "artifacts"
    result = packager.pack(pkg, output_dir=out, runner=_wheel_writing_runner())
    assert result.wheel_path.parent == out


def test_pack_ignores_preexisting_stale_wheel(tmp_path):
    pkg = _make_package(tmp_path)
    dist = pkg / "dist"
    dist.mkdir()
    stale = dist / "gaia_agent_demo_agent-0.0.9-py3-none-any.whl"
    stale.write_bytes(b"old")
    result = packager.pack(pkg, runner=_wheel_writing_runner())
    assert result.wheel_path.name == _EXPECTED_WHEEL
    assert result.version == "0.1.0"


# ---------------------------------------------------------------------------
# Fail-loudly paths
# ---------------------------------------------------------------------------


def test_pack_missing_directory(tmp_path):
    with pytest.raises(packager.PackagerError, match="not found"):
        packager.pack(tmp_path / "nope")


def test_pack_invalid_manifest(tmp_path):
    pkg = tmp_path / "demo-agent"
    pkg.mkdir()
    (pkg / "gaia-agent.yaml").write_text("id: BAD ID!!!", encoding="utf-8")
    with pytest.raises(packager.PackagerError, match="invalid"):
        packager.pack(pkg)


def test_pack_rejects_cpp_agent(tmp_path):
    pkg = _make_package(tmp_path, language="cpp", with_pyproject=False)
    with pytest.raises(packager.PackagerError, match="Python wheels"):
        packager.pack(pkg)


def test_pack_missing_pyproject(tmp_path):
    pkg = _make_package(tmp_path, with_pyproject=False)
    with pytest.raises(packager.PackagerError, match="pyproject.toml not found"):
        packager.pack(pkg)


def test_pack_build_failure_raises(tmp_path):
    pkg = _make_package(tmp_path)

    def _failing(cmd, cwd):
        return 1, "ERROR: build backend exploded"

    with pytest.raises(packager.PackagerError, match="build failed"):
        packager.pack(pkg, runner=_failing)


def test_pack_no_wheel_produced_raises(tmp_path):
    pkg = _make_package(tmp_path)

    def _noop(cmd, cwd):
        return 0, "did nothing"

    with pytest.raises(packager.PackagerError, match="no .whl"):
        packager.pack(pkg, runner=_noop)


def test_pack_wheel_name_mismatch_raises(tmp_path):
    pkg = _make_package(tmp_path)
    runner = _wheel_writing_runner(filename="something-else-9.9.9-py3-none-any.whl")
    with pytest.raises(packager.PackagerError, match="none matched"):
        packager.pack(pkg, runner=runner)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def test_normalize_wheel_stem():
    assert packager._normalize_wheel_stem("gaia-agent-summarize") == (
        "gaia_agent_summarize"
    )
    assert packager._normalize_wheel_stem("Gaia.Agent_Foo") == "gaia_agent_foo"


def test_read_pyproject_name(tmp_path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text(_PYPROJECT, encoding="utf-8")
    assert packager._read_pyproject_name(pp) == "gaia-agent-demo-agent"
