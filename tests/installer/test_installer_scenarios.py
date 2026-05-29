# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Installer scenario matrix tests (issue #992).

Parametrizes the post-install smoke checks across the OS x UV x Lemonade
matrix defined in issue #992. Each scenario validates:

  1. ``gaia --version`` exits 0 and prints a plausible version string.
  2. ``gaia init --non-interactive`` completes without error (when
     Lemonade Server is available).
  3. The installed gaia package is importable.

Scenarios that require self-hosted runners with AMD hardware (Lemonade,
NPU) are skipped on GitHub-hosted runners via the ``require_lemonade``
fixture or the ``GAIA_HAS_LEMONADE`` env var. The full 8-cell matrix
(2 OS x 2 UV x 2 Lemonade) is exercised on self-hosted runners labelled
``gaia-installer-matrix`` once the infrastructure from issue #991 lands.

UV installation methods:
  - "bundled": UV binary shipped inside the Electron installer (the
    desktop-app path). Simulated here by using the bundled UV fixture
    or the system UV if available.
  - "system": UV pre-installed on PATH by the user (the developer/CI
    path). The default for pytest runs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _has_uv() -> bool:
    return shutil.which("uv") is not None


def _has_lemonade() -> bool:
    if os.environ.get("GAIA_HAS_LEMONADE", "").lower() in ("1", "true", "yes"):
        return True
    return shutil.which("lemonade-server") is not None


def _gaia_cli() -> list[str]:
    configured = os.environ.get("GAIA_CLI")
    if configured:
        return [configured]
    installed = shutil.which("gaia")
    if installed:
        return [installed]
    return [sys.executable, "-m", "gaia.cli"]


# ---------------------------------------------------------------------------
# Parametrized scenario matrix
# ---------------------------------------------------------------------------

UV_METHODS = ["system", "bundled"]
LEMONADE_STATES = ["available", "unavailable"]


@pytest.mark.parametrize("uv_method", UV_METHODS, ids=lambda m: f"uv={m}")
@pytest.mark.parametrize(
    "lemonade_state", LEMONADE_STATES, ids=lambda s: f"lemonade={s}"
)
class TestInstallerScenarioMatrix:
    """Post-install smoke checks across UV x Lemonade scenarios.

    The ``bundled`` UV cell shadows PATH to route through the vendored UV
    binary under ``build/vendor/uv/``. Until self-hosted runners with the
    built vendor tree are provisioned (issue #991), the ``bundled`` cell
    skips on standard CI runners. When #991 lands the skip disappears and
    the cell actually exercises the Electron-installer UV path.
    """

    @staticmethod
    def _should_skip_uv(uv_method: str) -> str | None:
        if uv_method == "system" and not _has_uv():
            return "uv not on PATH — install uv or run on a provisioned runner"
        if uv_method == "bundled":
            bundled = REPO_ROOT / "src/gaia/apps/webui/build/vendor/uv"
            if not bundled.exists():
                return (
                    "bundled UV not built — run the build-installers workflow "
                    "or fetch the vendor/uv tree first"
                )
        return None

    @staticmethod
    def _should_skip_lemonade(lemonade_state: str) -> str | None:
        if lemonade_state == "available" and not _has_lemonade():
            return (
                "Lemonade Server not available — requires self-hosted runner "
                "with AMD hardware (issue #991)"
            )
        return None

    @staticmethod
    def _env_for_uv(uv_method: str) -> dict[str, str] | None:
        """Return an env overlay that routes through the bundled UV binary."""
        if uv_method != "bundled":
            return None
        vendor = REPO_ROOT / "src/gaia/apps/webui/build/vendor/uv"
        if not vendor.exists():
            return None
        # Prepend the vendor dir so the bundled uv shadows any system uv.
        env = os.environ.copy()
        candidates = list(vendor.rglob("uv")) + list(vendor.rglob("uv.exe"))
        if candidates:
            env["PATH"] = str(candidates[0].parent) + os.pathsep + env.get("PATH", "")
        return env

    def test_gaia_version(self, uv_method: str, lemonade_state: str):
        """``gaia --version`` exits 0 and prints a version string."""
        reason = self._should_skip_uv(uv_method)
        if reason:
            pytest.skip(reason)
        result = subprocess.run(
            [*_gaia_cli(), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            env=self._env_for_uv(uv_method),
        )
        assert (
            result.returncode == 0
        ), f"gaia --version failed (rc={result.returncode}): {result.stderr}"
        assert result.stdout.strip(), "gaia --version produced no output"

    def test_gaia_importable(self, uv_method: str, lemonade_state: str):
        """The gaia package is importable from the installed environment."""
        reason = self._should_skip_uv(uv_method)
        if reason:
            pytest.skip(reason)
        result = subprocess.run(
            [sys.executable, "-c", "import gaia; print(gaia.__file__)"],
            capture_output=True,
            text=True,
            timeout=30,
            env=self._env_for_uv(uv_method),
        )
        assert result.returncode == 0, f"import gaia failed: {result.stderr}"

    @pytest.mark.slow
    def test_gaia_init_yes(self, uv_method: str, lemonade_state: str):
        """``gaia init --yes`` completes when Lemonade is up."""
        reason = self._should_skip_uv(uv_method)
        if reason:
            pytest.skip(reason)
        reason = self._should_skip_lemonade(lemonade_state)
        if reason:
            pytest.skip(reason)
        if lemonade_state == "unavailable":
            pytest.skip(
                "gaia init requires Lemonade — skipped for "
                "lemonade=unavailable scenario"
            )
        result = subprocess.run(
            [*_gaia_cli(), "init", "--yes", "--skip-models"],
            capture_output=True,
            text=True,
            timeout=300,
            env=self._env_for_uv(uv_method),
        )
        assert result.returncode == 0, (
            f"gaia init --yes failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[-500:]}\n"
            f"stderr: {result.stderr[-500:]}"
        )


# ---------------------------------------------------------------------------
# Artifact format validation (issue #990 — can run without hardware)
# ---------------------------------------------------------------------------


class TestArtifactValidation:
    """Validate pre-release artifact properties that don't need hardware.

    These tests check structural properties of build artifacts: file
    format markers, minimum sizes, and metadata consistency. They run on
    any CI runner and catch the class of bug where ``pip install gaia``
    resolves to the published PyPI version instead of the candidate build.
    """

    @staticmethod
    def _artifact_dir() -> Path | None:
        env = os.environ.get("GAIA_INSTALLER_ARTIFACT_DIR")
        if env:
            p = Path(env)
            if p.is_dir():
                return p
        return None

    def test_wheel_is_well_formed(self):
        """If a .whl artifact is present, verify it's a valid zip with
        dist-info metadata."""
        artifact_dir = self._artifact_dir()
        if artifact_dir is None:
            pytest.skip("GAIA_INSTALLER_ARTIFACT_DIR not set")

        wheels = list(artifact_dir.glob("*.whl"))
        if not wheels:
            pytest.skip("No .whl files in artifact dir")

        for whl in wheels:
            assert (
                whl.stat().st_size > 10240
            ), f"{whl.name} is only {whl.stat().st_size} bytes — likely corrupt"
            with zipfile.ZipFile(whl) as zf:
                names = zf.namelist()
                has_metadata = any(
                    n.endswith("/METADATA") or n.endswith("/RECORD") for n in names
                )
                assert has_metadata, (
                    f"{whl.name} is missing dist-info METADATA/RECORD — "
                    "not a valid Python wheel"
                )

    def test_wheel_contains_gaia_package(self):
        """The built wheel must ship the ``gaia`` top-level package."""
        artifact_dir = self._artifact_dir()
        if artifact_dir is None:
            pytest.skip("GAIA_INSTALLER_ARTIFACT_DIR not set")

        wheels = list(artifact_dir.glob("*.whl"))
        if not wheels:
            pytest.skip("No .whl files in artifact dir")

        whl = wheels[0]
        with zipfile.ZipFile(whl) as zf:
            has_gaia = any(
                n.startswith("gaia/") or n.startswith("gaia\\") for n in zf.namelist()
            )
            assert has_gaia, f"{whl.name} does not contain a gaia/ top-level package"

    def test_update_manifest_yaml_parseable(self):
        """Update manifests (latest.yml, latest-mac.yml, latest-linux.yml)
        must be valid YAML with required electron-updater fields."""
        artifact_dir = self._artifact_dir()
        if artifact_dir is None:
            pytest.skip("GAIA_INSTALLER_ARTIFACT_DIR not set")

        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        manifests = list(artifact_dir.rglob("latest*.yml"))
        if not manifests:
            pytest.skip("No latest*.yml manifests in artifact dir")

        for manifest in manifests:
            content = manifest.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            assert isinstance(
                data, dict
            ), f"{manifest.name} did not parse as a YAML mapping"
            assert "version" in data, f"{manifest.name} is missing 'version' field"
            assert "files" in data or "path" in data, (
                f"{manifest.name} has neither 'files' nor 'path' — "
                "electron-updater will not find the installer"
            )
