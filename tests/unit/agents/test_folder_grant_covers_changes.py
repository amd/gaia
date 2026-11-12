# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""One "always allow changes in this folder" covers writing, editing and renaming there.

The prompt promised the folder, so a write grant that still asked before an
edit of a file beside it, or a rename inside it, asked three times for one
answer. The grant still names one folder: a subfolder, a sibling, a hidden or
code-running location, or a rename that leaves the folder is asked about.
"""

import os

import pytest

from gaia.agents.base.tool_grants import grant_scope


@pytest.fixture
def folder(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    home = root / "home"
    work = home / "work"
    (work / "sub").mkdir(parents=True)
    (home / "other").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", str(root / "bin"))
    (work / "draft_old.txt").write_text("draft")
    (work / "notes.txt").write_text("- owner: Dana\n")
    return work


def _file(tool, path, **extra):
    return grant_scope(
        tool, {"file_path": str(path), "_resolved_file_path": str(path), **extra}
    )


def _shell(command, **extra):
    return grant_scope("run_shell_command", {"command": command, **extra})


def _covers(granted, call):
    return call is not None and all(key in granted.keys for key in call.keys)


class TestWriteAndEdit:
    def test_a_write_grant_covers_an_edit_in_the_same_folder(self, folder):
        granted = _file("write_file", folder / "summary.md")
        assert _covers(granted, _file("edit_file", folder / "notes.txt"))

    def test_an_edit_grant_covers_a_write_in_the_same_folder(self, folder):
        granted = _file("edit_file", folder / "notes.txt")
        assert _covers(granted, _file("write_file", folder / "summary.md"))

    @pytest.mark.parametrize("where", ["sub", "../other"])
    def test_the_grant_stops_at_its_folder(self, folder, where):
        granted = _file("write_file", folder / "summary.md")
        elsewhere = (folder / where / "notes.txt").resolve()
        assert not _covers(granted, _file("edit_file", elsewhere))

    def test_an_exact_file_grant_stays_one_tool_on_one_file(self, folder):
        granted = grant_scope("write_file", {"file_path": "summary.md"})
        assert not _covers(
            granted, grant_scope("edit_file", {"file_path": "summary.md"})
        )

    def test_a_python_write_keeps_its_own_grant(self, folder):
        granted = _file("write_file", folder / "summary.md")
        assert not _covers(granted, _file("write_python_file", folder / "tool.py"))

    def test_the_offer_says_it_covers_changes(self, folder):
        scope = _file("write_file", folder / "summary.md")
        assert scope.phrase == f"changing files in {scope.subject[len('files in '):]}"
        assert scope.subject.startswith("files in ")

    def test_a_windows_folder_is_named_with_its_own_slashes(self):
        scope = grant_scope(
            "write_file",
            {
                "file_path": "summary.md",
                "_resolved_file_path": "C:\\Users\\me\\uxa\\summary.md",
            },
        )
        if scope is None or not any(k.endswith("/*") for k in scope.keys):
            pytest.skip("this OS does not resolve a Windows drive path as a folder")
        assert scope.subject == "files in C:\\Users\\me\\uxa"


class TestRename:
    def test_a_rename_in_the_folder_is_covered(self, folder):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f'ren "{folder / "draft_old.txt"}" draft_archive.txt')
        assert _covers(granted, call)

    @pytest.mark.parametrize("program", ["mv", "move"])
    def test_a_move_between_names_in_the_folder_is_covered(self, folder, program):
        granted = _file("write_file", folder / "summary.md")
        src, dst = folder / "draft_old.txt", folder / "draft_archive.txt"
        assert _covers(granted, _shell(f'{program} "{src}" "{dst}"'))

    def test_a_relative_rename_is_read_against_its_working_folder(self, folder):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(
            "mv draft_old.txt draft_archive.txt", working_directory=str(folder)
        )
        assert _covers(granted, call)

    def test_a_relative_rename_with_no_working_folder_is_asked(self, folder):
        granted = _file("write_file", folder / "summary.md")
        assert not _covers(granted, _shell("mv draft_old.txt draft_archive.txt"))

    @pytest.mark.parametrize(
        "dst",
        [
            lambda f: f / "sub" / "x.txt",
            lambda f: f.parent.parent / "other" / "x.txt",
            lambda f: f / "sub",
        ],
    )
    def test_a_move_out_of_the_folder_is_asked(self, folder, dst):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f'mv "{folder / "draft_old.txt"}" "{dst(folder)}"')
        assert not _covers(granted, call)

    @pytest.mark.parametrize(
        "command",
        [
            'mv -f "{f}/draft_old.txt" "{f}/a.txt"',
            'mv "{f}/draft_old.txt" "{f}/a.txt" && rm -rf "{f}"',
            'mv "{f}/draft_old.txt" "{f}/a.txt" > "{f}/log.txt"',
            'mv "{f}/*.txt" "{f}/a.txt"',
            'mv "$HOME/draft_old.txt" "{f}/a.txt"',
            'mv "{f}/draft_old.txt" "{f}/.hidden"',
            'mv "{f}/draft_old.txt" "{f}/a.txt" "{f}/b.txt"',
            'cp "{f}/draft_old.txt" "{f}/a.txt"',
            # The new name is taken: the rename would replace notes.txt.
            'mv "{f}/draft_old.txt" "{f}/notes.txt"',
        ],
    )
    def test_anything_but_a_plain_rename_is_asked(self, folder, command):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(command.format(f=folder))
        assert not _covers(granted, call)

    def test_a_rename_alone_still_offers_the_folder_it_stays_in(self, folder):
        call = _shell(f'ren "{folder / "draft_old.txt"}" draft_archive.txt')
        assert call.phrase.startswith("changing files in ")
        assert os.path.normcase(call.subject).endswith(os.path.normcase(folder.name))
