# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The desktop app ships its Lemonade archive; no installer runs a Lemonade MSI.

Nothing at runtime notices a broken bundle: the app quietly downloads instead.
These tests tie the build's fetch, electron-builder's extraResources and the
environment variable main.cjs sets to each other and to the Python reader.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

from gaia.llm.lemonade_embedded import (
    _ASSET_TEMPLATES,
    BUNDLED_ARCHIVE_DIR_ENV,
    EMBEDDABLE_SHA256,
)
from gaia.version import LEMONADE_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]
WEBUI = REPO_ROOT / "src" / "gaia" / "apps" / "webui"
FETCH_SCRIPT = REPO_ROOT / "installer" / "scripts" / "fetch_lemonade_embeddable.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-installers.yml"
INSTALLER_NSH = REPO_ROOT / "installer" / "nsis" / "installer.nsh"
ARCHIVE_DEST = "build/vendor/lemonade"
ARCHIVE_RESOURCE = "vendor/lemonade"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


@pytest.fixture
def fetcher():
    spec = importlib.util.spec_from_file_location(
        "fetch_lemonade_embeddable", FETCH_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Packaging ──────────────────────────────────────────────────────────────


def test_electron_builder_ships_the_archive_directory_the_build_fills():
    assert re.search(
        rf"- from: {ARCHIVE_DEST}\s+to: {ARCHIVE_RESOURCE}\b",
        _text(WEBUI / "electron-builder.yml"),
    )


def test_every_desktop_build_fetches_the_archive_into_that_directory():
    workflow = _text(WORKFLOW)
    assert f"--out src/gaia/apps/webui/{ARCHIVE_DEST}" in workflow
    platforms = re.findall(r"lemonade_platform: (\S+)", workflow)
    assert sorted(platforms) == ["linux-x64", "mac-arm64", "win-x64"]
    assert "Verify the Lemonade archive was packaged" in workflow


def test_main_points_the_backend_at_the_resource_directory():
    main = _text(WEBUI / "main.cjs")
    resource = '", "'.join(ARCHIVE_RESOURCE.split("/"))
    assert (
        f'{BUNDLED_ARCHIVE_DIR_ENV}: path.join(process.resourcesPath, "{resource}")'
        in main
    )


# ── Fetching ───────────────────────────────────────────────────────────────


def test_the_script_reads_the_same_pins_the_package_verifies_against(fetcher):
    assert fetcher.lemonade_version() == LEMONADE_VERSION
    assert fetcher.pinned_digests() == EMBEDDABLE_SHA256


@pytest.mark.parametrize("platform", ["win-x64", "linux-x64", "mac-arm64"])
def test_every_platform_resolves_to_a_published_pinned_asset(fetcher, platform):
    name = f"lemonade-embeddable-{LEMONADE_VERSION}-{fetcher.ASSET_SUFFIX[platform]}"
    assert name in EMBEDDABLE_SHA256
    published = {t.format(version=LEMONADE_VERSION) for t in _ASSET_TEMPLATES.values()}
    assert name in published


def _serve(fetcher, monkeypatch, data, digest):
    monkeypatch.setattr(fetcher, "download", lambda url, attempts: data)
    monkeypatch.setattr(
        fetcher,
        "pinned_digests",
        lambda: {
            f"lemonade-embeddable-{LEMONADE_VERSION}-windows-x64.zip": digest,
        },
    )


def test_stage_writes_the_verified_archive_and_clears_a_stale_one(
    fetcher, monkeypatch, tmp_path
):
    (tmp_path / "lemonade-embeddable-1.0.0-windows-x64.zip").write_bytes(b"stale")
    _serve(fetcher, monkeypatch, b"bytes", hashlib.sha256(b"bytes").hexdigest())

    dest = fetcher.stage("win-x64", tmp_path)

    assert dest.read_bytes() == b"bytes"
    assert [p.name for p in tmp_path.iterdir()] == [dest.name]


def test_stage_refuses_a_digest_mismatch_and_stages_nothing(
    fetcher, monkeypatch, tmp_path
):
    out = tmp_path / "out"
    _serve(fetcher, monkeypatch, b"bytes", "0" * 64)

    with pytest.raises(SystemExit, match="does not match EMBEDDABLE_SHA256"):
        fetcher.stage("win-x64", out)

    assert not out.exists()


def test_stage_refuses_a_version_with_no_pin(fetcher, monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "pinned_digests", lambda: {})
    with pytest.raises(SystemExit, match="no pinned SHA-256"):
        fetcher.stage("win-x64", tmp_path)


# ── No system Lemonade, no forced sign-in start ────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        INSTALLER_NSH,
        REPO_ROOT / "installer" / "tui" / "nsis" / "gaia-setup.nsi",
        REPO_ROOT / "installer" / "tui" / "nsis" / "build-setup.sh",
        REPO_ROOT / ".github" / "workflows" / "windows_setup.yml",
        WORKFLOW,
    ],
    ids=lambda p: p.name,
)
def test_no_installer_bundles_the_lemonade_msi(path):
    text = _text(path)
    assert "msiexec" not in text
    assert "lemonade-server-minimal" not in text


def _macro(name: str) -> str:
    body = re.search(
        rf"^!macro {name}\n(.*?)^!macroend", _text(INSTALLER_NSH), re.M | re.S
    )
    assert body, f"installer.nsh no longer defines {name}"
    return body.group(1)


def test_install_writes_the_run_value_only_to_put_a_users_back():
    # On an update the old uninstaller deletes the value before customInstall
    # runs, so reading it in customInit is what keeps it.
    assert "ReadRegStr $GaiaRunValue HKCU" in _macro("customInit")
    install = _macro("customInstall")
    assert re.search(
        r'\$\{If\} \$GaiaRunValue != ""\s+WriteRegStr HKCU "'
        + re.escape(RUN_KEY)
        + r'" \\\s+"\$\{PRODUCT_NAME\}" "\$GaiaRunValue"',
        install,
    )
    assert install.count("WriteRegStr") == 1
    # The one literal value is for a user who already chose it before the rename.
    assert install.count("--minimized") == 1
    assert re.search(
        r'\$\{If\} \$GaiaRunValue == ""\s+\$\{AndIf\} \$GaiaRunLegacy != ""\s+StrCpy',
        install,
    )


def test_uninstall_still_removes_the_run_value():
    assert re.search(
        r'DeleteRegValue HKCU "' + re.escape(RUN_KEY) + r'" \\\s+"\$\{PRODUCT_NAME\}"',
        _macro("customUnInstall"),
    )


def test_the_run_value_var_exists_only_where_it_is_used():
    # An unreferenced Var is a warning, and electron-builder builds with -WX.
    assert re.search(
        r"^!ifndef BUILD_UNINSTALLER\n\s*Var GaiaRunValue\n\s*Var GaiaRunLegacy\n!endif",
        _text(INSTALLER_NSH),
        re.M,
    )


def test_the_switch_names_the_run_value_the_installer_preserves():
    tray = _text(WEBUI / "services" / "tray-manager.cjs")
    product = re.search(
        r"^productName: (\S+)", _text(WEBUI / "electron-builder.yml"), re.M
    )
    assert f'const WIN_LOGIN_ITEM_NAME = "{product.group(1)}";' in tray
