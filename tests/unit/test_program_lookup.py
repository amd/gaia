# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A program installed while GAIA runs is found without restarting GAIA.

The process keeps the ``PATH`` it started with, so ``winget install Git.Git``
followed by ``git clone`` failed with "git is not installed" until a restart.
"""

import os
import sys

import pytest

from gaia import program_lookup

pytestmark = pytest.mark.real_path_refresh


@pytest.fixture(name="registry")
def _registry(monkeypatch, tmp_path):
    """A Windows PC whose registry ``Path`` is whatever the test sets."""
    entries = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(program_lookup, "_registry_path_entries", lambda: list(entries))
    # shutil.which reads sys.platform and needs the real Windows API under
    # "win32", so the folders on PATH are searched here instead.
    monkeypatch.setattr(program_lookup, "resolve", _find_on_path)
    start = tmp_path / "start"
    start.mkdir()
    monkeypatch.setenv("PATH", str(start))
    return entries


def _find_on_path(name):
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(folder, name)
        if folder and os.path.isfile(candidate):
            return candidate
    return None


def _program(folder, name="newtool"):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    target.write_text("")
    return target


def test_a_program_installed_after_startup_is_found(registry, tmp_path):
    installed = tmp_path / "Git" / "cmd"
    _program(installed)
    assert program_lookup.resolve("newtool") is None

    registry.append(str(installed))
    found = program_lookup.resolve_refreshing("newtool")

    assert found is not None
    assert os.path.dirname(found) == str(installed)


def test_the_plain_lookup_never_refreshes(registry, tmp_path):
    installed = tmp_path / "Git" / "cmd"
    _program(installed)
    registry.append(str(installed))
    before = os.environ["PATH"]

    assert program_lookup.resolve("newtool") is None
    assert os.environ["PATH"] == before


def test_new_entries_go_after_the_ones_already_there(registry, tmp_path):
    """Nothing that already resolves can be shadowed by a refreshed entry."""
    start = os.environ["PATH"]
    installed = tmp_path / "tools"
    installed.mkdir()
    registry.append(str(installed))

    added = program_lookup.refresh_path()

    assert added == [str(installed)]
    assert os.environ["PATH"] == os.pathsep.join([start, str(installed)])


def test_an_entry_already_on_path_is_not_added_again(registry):
    registry.append(os.environ["PATH"] + os.sep)

    assert program_lookup.refresh_path() == []


@pytest.mark.parametrize("entry", ["relative\\bin", "%NOPE%\\bin", ""])
def test_an_entry_that_names_no_real_folder_is_skipped(registry, entry):
    before = os.environ["PATH"]
    registry.append(entry)

    assert program_lookup.refresh_path() == []
    assert os.environ["PATH"] == before


def test_a_folder_that_does_not_exist_is_skipped(registry, tmp_path):
    registry.append(str(tmp_path / "never-created"))

    assert program_lookup.refresh_path() == []


def test_a_missing_program_stays_missing_and_path_is_unchanged(registry):
    before = os.environ["PATH"]

    assert program_lookup.resolve_refreshing("no-such-program-xyz") is None
    assert os.environ["PATH"] == before


def test_linux_does_not_refresh(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        program_lookup,
        "_registry_path_entries",
        lambda: pytest.fail("read the registry on Linux"),
    )

    assert program_lookup.refresh_path(after_install=True) == []


def test_macos_asks_the_login_shell_only_after_an_install(monkeypatch, tmp_path):
    brew = tmp_path / "homebrew" / "bin"
    brew.mkdir(parents=True)
    asked = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        program_lookup,
        "_login_path_entries",
        lambda: asked.append(1) or [str(brew)],
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    assert program_lookup.refresh_path() == []
    assert asked == []

    assert program_lookup.refresh_path(after_install=True) == [str(brew)]
    assert os.environ["PATH"].endswith(str(brew))
