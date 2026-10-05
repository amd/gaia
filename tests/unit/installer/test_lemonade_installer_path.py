# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Picking up an MSI's PATH change must not cost the process its own PATH.

``check_installation()`` re-reads PATH from the registry on Windows. It used to
replace the process PATH with the raw registry value, which dropped the active
venv's ``Scripts`` dir and anything the launching shell added, and left
``%SystemRoot%``-style entries unexpanded — so later tool lookups in the same
``gaia init`` resolved differently or not at all.
"""

import os
import sys

import pytest

from gaia.installer.lemonade_installer import LemonadeInstaller

USER_KEY = ("HKCU", "Environment")
SYSTEM_KEY = (
    "HKLM",
    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
)


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, paths, env):
        self.paths = paths
        self.env = env

    class _Key:
        def __init__(self, where):
            self.where = where

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, sub):  # noqa: N802
        return self._Key((root, sub))

    def QueryValueEx(self, key, name):  # noqa: N802
        assert name == "Path"
        if key.where not in self.paths:
            raise FileNotFoundError(name)
        return self.paths[key.where], 2

    def ExpandEnvironmentStrings(self, value):  # noqa: N802
        for var, expansion in self.env.items():
            value = value.replace(f"%{var}%", expansion)
        return value


@pytest.fixture
def windows_installer(monkeypatch):
    def make(paths, env=None):
        monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(paths, env or {}))
        installer = LemonadeInstaller()
        installer.system = "windows"
        return installer

    return make


def _path(*entries):
    """A Windows PATH value."""
    return ";".join(entries)


def test_the_process_path_is_kept_first_and_registry_entries_appended(
    windows_installer, monkeypatch
):
    monkeypatch.setenv("PATH", _path(r"C:\proj\.venv\Scripts", r"C:\Git\usr\bin"))
    installer = windows_installer(
        {
            USER_KEY: r"C:\Users\me\AppData\Local\lemonade_server\bin",
            SYSTEM_KEY: r"%SystemRoot%\system32;%SystemRoot%",
        },
        env={"SystemRoot": r"C:\Windows"},
    )

    installer.refresh_path_from_registry()

    assert os.environ["PATH"] == _path(
        r"C:\proj\.venv\Scripts",
        r"C:\Git\usr\bin",
        r"C:\Users\me\AppData\Local\lemonade_server\bin",
        r"C:\Windows\system32",
        r"C:\Windows",
    )


def test_an_entry_already_on_path_is_not_added_twice(windows_installer, monkeypatch):
    monkeypatch.setenv("PATH", _path(r"C:\Windows\System32\\", r"C:\tools"))
    installer = windows_installer({SYSTEM_KEY: r"c:\windows\system32;;C:\tools"})

    installer.refresh_path_from_registry()

    assert os.environ["PATH"] == _path(r"C:\Windows\System32\\", r"C:\tools")


def test_no_registry_path_leaves_path_alone(windows_installer, monkeypatch):
    monkeypatch.setenv("PATH", r"C:\only")
    installer = windows_installer({})

    installer.refresh_path_from_registry()

    assert os.environ["PATH"] == r"C:\only"
