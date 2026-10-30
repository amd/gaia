# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import sys

import pytest

from gaia.utils import user_folders


def test_documents_dir_uses_windows_known_folder(monkeypatch, tmp_path):
    home = tmp_path / "home"
    one_drive_documents = home / "OneDrive" / "Documents"
    monkeypatch.setattr(user_folders.sys, "platform", "win32")
    monkeypatch.setattr(user_folders.Path, "home", lambda: home)
    monkeypatch.setattr(user_folders, "_windows_profile_dir", lambda: home)
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
        user_folders, "_windows_profile_dir", lambda: tmp_path / "current"
    )
    monkeypatch.setattr(
        user_folders,
        "_windows_documents_dir",
        lambda: tmp_path / "current" / "OneDrive" / "Documents",
    )

    assert user_folders.documents_dir(home) == home / "Documents"


def test_a_sandboxed_home_never_gets_the_real_users_documents(monkeypatch, tmp_path):
    # A test or sandbox swaps HOME; the signed-in user's OneDrive must not leak in.
    sandbox = tmp_path / "sandbox"
    monkeypatch.setattr(user_folders.sys, "platform", "win32")
    monkeypatch.setattr(user_folders.Path, "home", lambda: sandbox)
    monkeypatch.setattr(
        user_folders, "_windows_profile_dir", lambda: tmp_path / "real-user"
    )
    monkeypatch.setattr(
        user_folders,
        "_windows_documents_dir",
        lambda: tmp_path / "real-user" / "OneDrive" / "Documents",
    )

    assert user_folders.documents_dir() == sandbox / "Documents"


def test_an_unresolvable_profile_keeps_documents_under_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(user_folders.sys, "platform", "win32")
    monkeypatch.setattr(user_folders, "_windows_profile_dir", lambda: None)
    monkeypatch.setattr(
        user_folders, "_windows_documents_dir", lambda: tmp_path / "elsewhere"
    )

    assert user_folders.documents_dir(home) == home / "Documents"


@pytest.mark.parametrize("value", ['"$HOME/"', '"$HOME"', '"${HOME}"', "$HOME/."])
def test_a_disabled_xdg_documents_folder_is_not_the_whole_home(
    monkeypatch, tmp_path, value
):
    # xdg-user-dirs disables a folder by setting it to $HOME.
    home = tmp_path / "home"
    config = home / ".config"
    config.mkdir(parents=True)
    (config / "user-dirs.dirs").write_text(
        f"XDG_DOCUMENTS_DIR={value}\n", encoding="utf-8"
    )
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    assert user_folders.documents_dir(home) == home / "Documents"


def test_xdg_comments_and_other_folders_are_ignored(monkeypatch, tmp_path):
    home = tmp_path / "home"
    elsewhere = tmp_path / "data" / "docs"
    config = home / ".config"
    config.mkdir(parents=True)
    (config / "user-dirs.dirs").write_text(
        '# XDG_DOCUMENTS_DIR="$HOME/Wrong"\n'
        'XDG_DESKTOP_DIR="$HOME/Desktop"\n'
        f'XDG_DOCUMENTS_DIR="{elsewhere}"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    assert user_folders.documents_dir(home) == elsewhere


def test_unreadable_xdg_file_keeps_documents_under_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "user-dirs.dirs").mkdir(parents=True)  # a dir, not a file
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    assert user_folders.documents_dir(home) == home / "Documents"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders")
def test_windows_known_folders_resolve_on_a_real_machine():
    profile = user_folders._windows_profile_dir()
    documents = user_folders._windows_documents_dir()

    assert profile is not None and profile.is_absolute()
    assert documents is not None and documents.is_absolute()
    assert documents != profile


def test_the_ui_documents_quick_link_follows_the_redirect(monkeypatch, tmp_path):
    ui_utils = pytest.importorskip("gaia.ui.utils")
    home = tmp_path / "home"
    redirected = home / "OneDrive" / "Documents"
    redirected.mkdir(parents=True)
    (home / "Documents").mkdir()
    monkeypatch.setattr(ui_utils.Path, "home", lambda: home)
    monkeypatch.setattr(ui_utils, "documents_dir", lambda _home: redirected)

    links = {link.name: link.path for link in ui_utils.build_quick_links()}

    assert links["Documents"] == str(redirected)


def test_the_ui_hides_documents_outside_document_roots(monkeypatch, tmp_path):
    ui_utils = pytest.importorskip("gaia.ui.utils")
    home = tmp_path / "home"
    home.mkdir()
    redirected = tmp_path / "external-documents"
    redirected.mkdir()
    monkeypatch.setattr(ui_utils.Path, "home", lambda: home)
    monkeypatch.setattr(ui_utils, "documents_dir", lambda _home: redirected)
    monkeypatch.setattr(ui_utils, "document_roots", lambda: [home.resolve()])

    links = {link.name for link in ui_utils.build_quick_links()}

    assert "Documents" not in links
