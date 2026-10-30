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

    if profile is None or documents is None:
        pytest.skip("known folders unavailable under a sandboxed USERPROFILE")
    assert profile.is_absolute()
    assert documents.is_absolute()
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


def test_a_symlinked_documents_escaping_the_roots_is_not_browseable(tmp_path):
    ui_utils = pytest.importorskip("gaia.ui.utils")
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = home / "Documents"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges on this host")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ui_utils, "document_roots", lambda: [home.resolve()])
        assert ui_utils.is_within_document_roots(link) is False
        assert ui_utils.is_within_document_roots(home / "Downloads") is True


def test_an_xdg_traversal_out_of_home_gets_no_ui_quick_link(monkeypatch, tmp_path):
    ui_utils = pytest.importorskip("gaia.ui.utils")
    home = tmp_path / "home"
    config = home / ".config"
    config.mkdir(parents=True)
    (tmp_path / "escaped").mkdir()
    (config / "user-dirs.dirs").write_text(
        'XDG_DOCUMENTS_DIR="$HOME/../escaped"\n', encoding="utf-8"
    )
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    monkeypatch.setattr(ui_utils.Path, "home", lambda: home)
    monkeypatch.setattr(ui_utils, "document_roots", lambda: [home.resolve()])

    links = {link.name for link in ui_utils.build_quick_links()}

    assert "Documents" not in links


def test_a_crlf_user_dirs_file_still_resolves(monkeypatch, tmp_path):
    home = tmp_path / "home"
    config = home / ".config"
    config.mkdir(parents=True)
    (config / "user-dirs.dirs").write_bytes(b'XDG_DOCUMENTS_DIR="$HOME/Docs"\r\n')
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    assert user_folders.documents_dir(home) == home / "Docs"


def _write_user_dirs(home, data: bytes):
    config = home / ".config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "user-dirs.dirs").write_bytes(data)


def test_a_non_utf8_user_dirs_file_never_raises(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _write_user_dirs(home, b'XDG_DOCUMENTS_DIR="$HOME/Dok\xffumente"\n')
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    docs = user_folders.documents_dir(home)

    # POSIX keeps the raw byte as a surrogate; elsewhere it falls back.
    assert docs.parent == home


def test_a_nul_byte_in_the_xdg_value_falls_back(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _write_user_dirs(home, b'XDG_DOCUMENTS_DIR="$HOME/Docs\x00/../../etc"\n')
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    assert user_folders.documents_dir(home) == home / "Documents"


def test_a_nul_byte_in_home_never_raises(monkeypatch):
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    home = user_folders.Path("/home/u\x00ser")

    assert user_folders.documents_dir(home) == home / "Documents"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("$HOME/Docs", "Docs"),
        ("${HOME}/Docs", "Docs"),
        ("~/Docs", "Docs"),
        ("$HOMEevil/Docs", "$HOMEevil/Docs"),
        ("~evil/Docs", "~evil/Docs"),
    ],
)
def test_home_expands_only_as_a_whole_prefix(monkeypatch, tmp_path, value, expected):
    home = tmp_path / "home"
    _write_user_dirs(home, f'XDG_DOCUMENTS_DIR="{value}"\n'.encode())
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    # ``~`` must come from the home passed in, never this process' HOME.
    monkeypatch.setattr(user_folders.Path, "home", lambda: tmp_path / "real")

    assert user_folders.documents_dir(home) == home / expected


def test_xdg_config_home_is_honoured_for_the_current_user(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "xdg"
    config.mkdir()
    (config / "user-dirs.dirs").write_bytes(b'XDG_DOCUMENTS_DIR="$HOME/Papers"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    monkeypatch.setattr(user_folders, "_account_home", lambda: home)

    assert user_folders.documents_dir(home) == home / "Papers"


def test_xdg_config_home_never_leaks_into_a_sandbox_home(monkeypatch, tmp_path):
    real, sandbox = tmp_path / "real", tmp_path / "sandbox"
    real.mkdir()
    sandbox.mkdir()
    config = tmp_path / "xdg"
    config.mkdir()
    (config / "user-dirs.dirs").write_bytes(b'XDG_DOCUMENTS_DIR="/srv/real-docs"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    monkeypatch.setattr(user_folders, "_account_home", lambda: real)

    assert user_folders.documents_dir(sandbox) == sandbox / "Documents"


def test_a_relative_xdg_config_home_is_ignored(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _write_user_dirs(home, b'XDG_DOCUMENTS_DIR="$HOME/Mine"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/xdg")
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    monkeypatch.setattr(user_folders, "_account_home", lambda: home)

    assert user_folders.documents_dir(home) == home / "Mine"


def test_quick_links_survive_a_corrupt_user_dirs_file(monkeypatch, tmp_path):
    ui_utils = pytest.importorskip("gaia.ui.utils")
    home = tmp_path / "home"
    _write_user_dirs(home, b'XDG_DOCUMENTS_DIR="\xff\xfe\x00"\n')
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    monkeypatch.setattr(ui_utils.Path, "home", lambda: home)
    monkeypatch.setattr(ui_utils, "document_roots", lambda: [home.resolve()])

    links = {link.name for link in ui_utils.build_quick_links()}

    assert "Home" in links


def test_a_unc_documents_outside_the_roots_is_never_touched(monkeypatch, tmp_path):
    """Probing an unreachable SMB share can hang for its whole timeout."""
    ui_utils = pytest.importorskip("gaia.ui.utils")
    home = tmp_path / "home"
    home.mkdir()
    unc = ui_utils.Path(r"\fileserver\share\Documents")
    monkeypatch.setattr(ui_utils.Path, "home", lambda: home)
    monkeypatch.setattr(ui_utils, "documents_dir", lambda _home: unc)
    monkeypatch.setattr(ui_utils, "document_roots", lambda: [home.resolve()])
    touched = []
    real_is_dir, real_resolve = ui_utils.Path.is_dir, ui_utils.Path.resolve

    def is_dir(self, *a, **k):
        if "fileserver" in str(self):
            touched.append(("is_dir", self))
            return True
        return real_is_dir(self, *a, **k)

    def resolve(self, *a, **k):
        if "fileserver" in str(self):
            touched.append(("resolve", self))
        return real_resolve(self, *a, **k)

    monkeypatch.setattr(ui_utils.Path, "is_dir", is_dir)
    monkeypatch.setattr(ui_utils.Path, "resolve", resolve)

    links = {link.name for link in ui_utils.build_quick_links()}

    assert "Documents" not in links
    assert touched == []


def _inherited_xdg_config(monkeypatch, tmp_path):
    """A sandbox HOME that still inherits the real account's XDG_CONFIG_HOME."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    config = tmp_path / "real-xdg"
    config.mkdir()
    (config / "user-dirs.dirs").write_bytes(b'XDG_DOCUMENTS_DIR="/srv/real-docs"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.setattr(user_folders.Path, "home", lambda: sandbox)
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    return sandbox


def test_a_sandboxed_home_ignores_the_inherited_xdg_config_home(
    monkeypatch, tmp_path
):
    """Swapping HOME must not hand back the real account's Documents."""
    sandbox = _inherited_xdg_config(monkeypatch, tmp_path)

    assert user_folders.documents_dir() == sandbox / "Documents"
    assert user_folders.documents_dir(sandbox) == sandbox / "Documents"


def test_an_unknown_account_never_honours_xdg_config_home(monkeypatch, tmp_path):
    sandbox = _inherited_xdg_config(monkeypatch, tmp_path)
    monkeypatch.setattr(user_folders, "_account_home", lambda: None)

    assert user_folders.documents_dir(sandbox) == sandbox / "Documents"


@pytest.mark.skipif(not hasattr(__import__("os"), "getuid"), reason="POSIX account")
def test_account_home_comes_from_the_password_database(monkeypatch, tmp_path):
    import pwd

    monkeypatch.setenv("HOME", str(tmp_path))
    expected = pwd.getpwuid(__import__("os").getuid()).pw_dir

    assert str(user_folders._account_home()) == expected


def test_the_xdg_documents_path_is_never_touched_while_resolving(
    monkeypatch, tmp_path
):
    """A Documents folder on a dead network mount must not stall the lookup."""
    home = tmp_path / "home"
    dead = tmp_path / "dead-share" / "Docs"
    _write_user_dirs(home, f'XDG_DOCUMENTS_DIR="{dead}"\n'.encode())
    monkeypatch.setattr(user_folders.sys, "platform", "linux")
    touched = []
    real_resolve, real_stat = user_folders.Path.resolve, user_folders.Path.stat

    def resolve(self, *a, **k):
        if "dead-share" in str(self):
            touched.append(("resolve", str(self)))
        return real_resolve(self, *a, **k)

    def stat(self, *a, **k):
        if "dead-share" in str(self):
            touched.append(("stat", str(self)))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(user_folders.Path, "resolve", resolve)
    monkeypatch.setattr(user_folders.Path, "stat", stat)

    assert user_folders.documents_dir(home) == dead
    assert touched == []


def test_a_trailing_slash_home_still_counts_as_disabled(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _write_user_dirs(home, b'XDG_DOCUMENTS_DIR="$HOME/"\n')
    monkeypatch.setattr(user_folders.sys, "platform", "linux")

    assert user_folders.documents_dir(home) == home / "Documents"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders")
def test_known_folder_lookup_does_not_verify_the_folder(monkeypatch):
    """A redirect to a dead share must not be probed by the shell lookup."""
    import ctypes

    seen = []

    class _Shell32:
        @staticmethod
        def SHGetKnownFolderPath(_rfid, flags, _token, _out):
            seen.append(flags)
            return 1  # failure: no path is handed back

    monkeypatch.setattr(ctypes.windll, "shell32", _Shell32())

    assert user_folders._windows_documents_dir() is None
    assert seen == [0x4000]
