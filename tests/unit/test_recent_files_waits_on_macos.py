# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``list_recent_files`` says macOS is asking instead of freezing the turn (#5607).

Listing ``~/Documents`` is the tool's job, so it can't skip the folder the way
an undirected search does (#5578). The first read on a fresh Mac blocks behind
"GAIA would like to access files in your Documents folder". The read is probed
on a thread, so the tool reports the open dialog and lists the other folders.
The platform is patched and the home folder is a temp dir, so these run
anywhere and never touch the real home.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.verification import check_was_executed
from gaia.agents.tools import protected_folders
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.protected_folders import (
    ASKING,
    DENIED,
    GRANTED,
    is_macos_guarded,
    macos_access,
)


class _Sandbox:
    def __init__(self, *paths):
        self.allowed_paths = {Path(p).resolve() for p in paths}

    def is_path_allowed(self, path, prompt_user=True):
        resolved = Path(path).resolve()
        return any(
            resolved == root or root in resolved.parents for root in self.allowed_paths
        )

    def validate_read(self, path, prompt_user=True):
        if not self.is_path_allowed(path, prompt_user=prompt_user):
            return False, f"Access denied: '{path}' is not in allowed paths"
        return True, ""


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = (tmp_path / "home").resolve()
    for name in ("Documents", "Downloads", "Desktop"):
        (home / name).mkdir(parents=True)
        (home / name / f"{name.lower()}_note.md").write_text("x\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(protected_folders, "_probes", {})
    return home


@pytest.fixture
def dialog(monkeypatch):
    """Reads of folders in ``held`` block until ``answer`` is set, like TCC; ``refused`` ones are denied."""
    state = {"held": set(), "refused": set(), "answer": threading.Event(), "reads": []}
    real = protected_folders._read_first_entry

    def read(folder):
        state["reads"].append(folder.name)
        if folder.name in state["held"]:
            state["answer"].wait(10)
        if folder.name in state["refused"]:
            return DENIED
        return real(folder)

    monkeypatch.setattr(protected_folders, "_read_first_entry", read)
    monkeypatch.setattr(protected_folders, "MACOS_PROMPT_WAIT_S", 0.05)
    yield state
    state["answer"].set()


def _recent_files(*allowed):
    mixin = FileSearchToolsMixin()
    mixin.path_validator = _Sandbox(*allowed)
    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_file_search_tools()
        return _TOOL_REGISTRY["list_recent_files"]["function"]
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _state(folder, wait=1):
    return macos_access([folder], wait=wait).get(Path(folder).resolve())


def test_a_granted_folder_answers_granted(home, dialog):
    assert _state(home / "Documents") == GRANTED


def test_a_read_held_by_the_dialog_is_pending_and_clears_once_answered(home, dialog):
    dialog["held"].add("Documents")

    assert _state(home / "Documents", wait=0.05) == ASKING

    dialog["answer"].set()
    protected_folders._probes[(home / "Documents").resolve()].join(2)
    assert _state(home / "Documents") == GRANTED


def test_asking_again_while_the_dialog_is_open_reuses_the_probe(home, dialog):
    dialog["held"].add("Documents")

    _state(home / "Documents", wait=0.05)
    _state(home / "Documents", wait=0.05)

    assert dialog["reads"] == ["Documents"]


def test_only_macos_guarded_folders_are_probed(home, dialog, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()

    assert macos_access([other]) == {}
    assert dialog["reads"] == []
    assert is_macos_guarded(home / "Downloads")
    assert not is_macos_guarded(other)


def test_nothing_is_probed_off_macos(home, dialog, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    assert macos_access([home / "Documents"]) == {}
    assert dialog["reads"] == []


def test_the_other_folders_are_listed_while_one_waits(home, dialog):
    dialog["held"].add("Documents")

    result = _recent_files(home)()

    assert result["status"] == "success"
    assert sorted(f["file_name"] for f in result["files"]) == [
        "desktop_note.md",
        "downloads_note.md",
    ]
    assert result["locations_waiting_for_macos"] == ["Documents"]
    assert "Documents" not in result["locations_searched"]
    assert "macOS is asking whether GAIA may read ~/Documents" in (
        result["display_message"]
    )


def test_every_folder_waiting_is_an_error_that_says_what_to_do(home, dialog):
    dialog["held"].update({"Documents", "Downloads", "Desktop"})

    result = _recent_files(home)()

    assert result["status"] == "error"
    assert check_was_executed(result) is False
    assert "~/Documents, ~/Downloads, ~/Desktop" in result["error"]
    assert "answer that dialog" in result["error"]


def test_no_note_when_nothing_waits(home, dialog):
    result = _recent_files(home)()

    assert "locations_waiting_for_macos" not in result
    assert "macOS is asking" not in result["display_message"]


def test_every_folder_waits_against_one_shared_deadline(home, dialog, monkeypatch):
    import time

    monkeypatch.setattr(protected_folders, "MACOS_PROMPT_WAIT_S", 0.3)
    dialog["held"].update({"Documents", "Downloads", "Desktop"})

    started = time.monotonic()
    states = macos_access([home / n for n in ("Documents", "Downloads", "Desktop")])

    assert set(states.values()) == {ASKING}
    assert time.monotonic() - started < 0.6


def test_a_folder_macos_denied_says_where_to_allow_it(home, dialog):
    dialog["refused"].add("Documents")

    result = _recent_files(home)()

    assert result["status"] == "success"
    assert result["locations_denied_by_macos"] == ["Documents"]
    assert "Documents" not in result["locations_searched"]
    assert "Privacy & Security > Files and Folders" in result["display_message"]
    assert sorted(f["file_name"] for f in result["files"]) == [
        "desktop_note.md",
        "downloads_note.md",
    ]


def test_a_folder_outside_the_sandbox_is_never_probed(home, dialog):
    result = _recent_files(home / "Downloads")()

    assert dialog["reads"] == ["Downloads"]
    assert set(result["locations_not_searched"]) == {"Documents", "Desktop"}
