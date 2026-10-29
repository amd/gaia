# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

from pathlib import Path

from gaia.utils import user_folders


def test_documents_dir_uses_windows_known_folder(monkeypatch, tmp_path):
    home = tmp_path / "home"
    one_drive_documents = home / "OneDrive" / "Documents"
    monkeypatch.setattr(user_folders.sys, "platform", "win32")
    monkeypatch.setattr(user_folders.Path, "home", lambda: home)
    monkeypatch.setattr(
        user_folders, "_windows_documents_dir", lambda: one_drive_documents
    )

    assert user_folders.documents_dir() == one_drive_documents


def test_documents_dir_reads_linux_xdg_configuration(monkeypatch, tmp_path):
    home = tmp_path / "home"
    config = home / ".config"
    config.mkdir(parents=True)
    (config / "user-dirs.dirs").write_text(
        'XDG_DOCUMENTS_DIR="$HOME/Dokumente"\n', encoding="utf-8"
    )
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    assert user_folders.documents_dir(home) == home / "Dokumente"


def test_documents_dir_falls_back_when_platform_configuration_is_unavailable(
    monkeypatch, tmp_path
):
    home = tmp_path / "home"
    monkeypatch.setattr(user_folders.sys, "platform", "win32")
    monkeypatch.setattr(user_folders, "_windows_documents_dir", lambda: None)

    assert user_folders.documents_dir(home) == home / "Documents"


def test_documents_dir_ignores_an_empty_linux_setting(monkeypatch, tmp_path):
    home = tmp_path / "home"
    config = home / ".config"
    config.mkdir(parents=True)
    (config / "user-dirs.dirs").write_text('XDG_DOCUMENTS_DIR=""\n', encoding="utf-8")
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    assert user_folders.documents_dir(home) == home / "Documents"


def test_documents_dir_keeps_an_explicit_non_current_windows_home_local(
    monkeypatch, tmp_path
):
    home = tmp_path / "other-user"
    monkeypatch.setattr(user_folders.sys, "platform", "win32")
    monkeypatch.setattr(
        user_folders,
        "_windows_documents_dir",
        lambda: tmp_path / "current" / "OneDrive" / "Documents",
    )

    assert user_folders.documents_dir(home) == home / "Documents"
