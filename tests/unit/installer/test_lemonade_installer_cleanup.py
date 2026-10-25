# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression tests for cleaning up failed Lemonade installer downloads."""

import os
import time

import pytest

from gaia.installer.lemonade_installer import (
    KEPT_INSTALLER_MIN_AGE_S,
    discard_kept_installers,
)
from tests.fixtures.lemonade_assets import SUPPORTED_TARGETS, make_installer


@pytest.mark.parametrize(
    "system,machine,minimal,expected_asset",
    SUPPORTED_TARGETS,
    ids=lambda value: str(value),
)
def test_cleanup_deletes_names_created_by_download_installer(
    tmp_path, monkeypatch, system, machine, minimal, expected_asset
):
    installer = make_installer(system, machine, minimal)
    monkeypatch.setattr(installer, "verify_download_url", lambda url: url)
    monkeypatch.setattr(
        installer,
        "_fetch_verified",
        lambda url, path, expected_sha256: path.write_bytes(b"installer"),
    )

    downloaded = installer.download_installer(dest_dir=str(tmp_path))

    assert downloaded.name.startswith(f"{expected_asset.rsplit('.', 1)[0]}-")
    assert downloaded.suffix == f".{expected_asset.rsplit('.', 1)[1]}"
    old = time.time() - 2 * KEPT_INSTALLER_MIN_AGE_S
    os.utime(downloaded, (old, old))

    discard_kept_installers(tmp_path, older_than=time.time())

    assert not downloaded.exists()


def test_cleanup_keeps_a_matching_installer_newer_than_one_hour(tmp_path):
    installer = tmp_path / "lemonade-1234abcd.msi"
    installer.write_bytes(b"in progress")
    recent = time.time() - KEPT_INSTALLER_MIN_AGE_S / 2
    os.utime(installer, (recent, recent))

    discard_kept_installers(tmp_path, older_than=time.time())

    assert installer.exists()


def test_cleanup_keeps_a_matching_installer_newer_than_cutoff(tmp_path):
    now = time.time()
    installer = tmp_path / "lemonade-1234abcd.msi"
    installer.write_bytes(b"finished before cleanup started")
    modified = now - 2 * KEPT_INSTALLER_MIN_AGE_S
    os.utime(installer, (modified, modified))

    discard_kept_installers(tmp_path, older_than=now - 3 * KEPT_INSTALLER_MIN_AGE_S)

    assert installer.exists()


def test_cleanup_leaves_unrelated_old_files_alone(tmp_path):
    unrelated = tmp_path / "unrelated-installer.msi"
    unrelated.write_bytes(b"keep")
    old = time.time() - 2 * KEPT_INSTALLER_MIN_AGE_S
    os.utime(unrelated, (old, old))

    discard_kept_installers(tmp_path, older_than=time.time())

    assert unrelated.exists()
