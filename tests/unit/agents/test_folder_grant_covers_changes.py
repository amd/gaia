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


WINDOWS = os.name == "nt"
windows_only = pytest.mark.skipif(not WINDOWS, reason="cmd.exe's ren and move")


class TestRename:
    @windows_only
    def test_a_rename_in_the_folder_is_covered(self, folder):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f'ren "{folder / "draft_old.txt"}" draft_archive.txt')
        assert _covers(granted, call)

    @pytest.mark.parametrize(
        "program", ["mv", pytest.param("move", marks=windows_only)]
    )
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
        call = _shell(f'mv "{folder / "draft_old.txt"}" "{folder / "a.txt"}"')
        assert call.phrase.startswith("changing files in ")
        assert os.path.normcase(call.subject).endswith(os.path.normcase(folder.name))


class TestRenameCannotReachFurther:
    """Shapes that look like a rename in the granted folder but do more."""

    @pytest.mark.skipif(WINDOWS, reason="cmd.exe's rename is a built-in")
    def test_perl_rename_runs_code_and_is_asked(self, folder):
        # Perl's `rename` evaluates its first argument, so it is never a plain rename.
        code = "s{^}{system q{touch pwned}}e"
        (folder / code).write_text("")
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f"rename '{code}' x", working_directory=str(folder))
        assert not _covers(granted, call)

    @pytest.mark.parametrize("program", ["rename-item", "move-item"])
    def test_powershell_cmdlets_are_not_renames(self, folder, program):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f'{program} "{folder / "draft_old.txt"}" "{folder / "a.txt"}"')
        assert not _covers(granted, call)

    def test_renaming_a_subfolder_is_asked(self, folder):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f'mv "{folder / "sub"}" "{folder / "sub2"}"')
        assert not _covers(granted, call)

    def test_a_missing_source_is_asked(self, folder):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f'mv "{folder / "absent.txt"}" "{folder / "a.txt"}"')
        assert not _covers(granted, call)

    def test_a_linked_source_is_asked(self, folder):
        outside = folder.parent / "other" / "secret.txt"
        outside.write_text("secret")
        link = folder / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("this account cannot create symlinks")
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f'mv "{link}" "{folder / "a.txt"}"')
        assert not _covers(granted, call)

    @pytest.mark.parametrize(
        "name", ["con", "NUL", "con.txt", "lpt1.log", "a.txt.", "a.txt ", "a.txt:x"]
    )
    def test_device_names_and_names_windows_rewrites_are_asked(self, folder, name):
        granted = _file("write_file", folder / "summary.md")
        call = _shell(f'mv "{folder / "draft_old.txt"}" "{folder}{os.sep}{name}"')
        assert not _covers(granted, call)

    @windows_only
    @pytest.mark.parametrize(
        "command, cwd",
        [
            (r"mv \\attacker\s\a.txt \\attacker\s\b.txt", None),
            ("mv a.txt b.txt", r"\\attacker\s"),
            (r"ren \\?\UNC\attacker\s\a.txt b.txt", None),
            ("mv //attacker/s/a.txt //attacker/s/b.txt", None),
        ],
    )
    def test_a_network_path_is_asked_without_being_looked_at(
        self, monkeypatch, command, cwd
    ):
        looked = []

        def record(real):
            def wrapper(path, *args, **kwargs):
                looked.append(str(path))
                return real(path, *args, **kwargs)

            return wrapper

        for name in ("isfile", "islink", "lexists", "realpath", "exists"):
            monkeypatch.setattr(os.path, name, record(getattr(os.path, name)))
        extra = {"working_directory": cwd} if cwd else {}
        scope = _shell(command, **extra)
        assert scope is None or not any(
            k.startswith("file_change:") for k in scope.keys
        )
        assert not [p for p in looked if "attacker" in p]
