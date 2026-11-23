# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Credential folders stay closed to scripts and to a second name for their files.

A script that builds the path at run time, or a hard link to a credential
placed elsewhere, reaches the same file as the plain path the tools refuse.
"""

from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

pytest.importorskip("gaiapy")

from gaiapy.python_runner import (  # noqa: E402
    GUARDED_FOLDERS_ENV,
    guarded_open_refusal,
    python_command,
)

from gaia import security  # noqa: E402
from gaia.security import PathValidator  # noqa: E402


@pytest.fixture
def home(tmp_path):
    secret = tmp_path / ".keys"
    secret.mkdir()
    (secret / "id_decoy").write_text("SECRET-BYTES")
    work = tmp_path / "work"
    work.mkdir()
    (work / "notes.txt").write_text("plain")
    return tmp_path


def _run(code, home, tmp_path):
    script = tmp_path / "snippet.py"
    script.write_text(textwrap.dedent(code), encoding="utf-8")
    env = {**os.environ, GUARDED_FOLDERS_ENV: str(home / ".keys")}
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        python_command(script),
        cwd=str(home / "work"),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    "open_expr",
    [
        "open(os.path.join(HOME, '.k{}ys'.format('e'), 'id_decoy')).read()",
        "open(HOME + os.sep + chr(46) + 'keys' + os.sep + 'id_decoy').read()",
        "open(glob.glob(os.path.join(HOME, '.k*', 'id_d*'))[0]).read()",
        "Path(HOME, '.keys', 'id_decoy').read_bytes()",
        "os.read(os.open(os.path.join(HOME, '.keys', 'id_decoy'), os.O_RDONLY), 99)",
    ],
)
def test_a_path_built_at_run_time_is_refused_when_opened(home, tmp_path, open_expr):
    code = f"""
        import glob, os
        from pathlib import Path
        HOME = {str(home)!r}
        print({open_expr})
        """
    run = _run(code, home, tmp_path)
    assert run.returncode == 1
    assert "SECRET-BYTES" not in run.stdout
    assert "PermissionError" in run.stderr


def test_a_walk_that_opens_what_it_finds_is_refused(home, tmp_path):
    code = f"""
        import os
        for root, _dirs, files in os.walk({str(home)!r}):
            for name in files:
                if name.startswith('id_'):
                    print(open(os.path.join(root, name)).read())
        """
    run = _run(code, home, tmp_path)
    assert "SECRET-BYTES" not in run.stdout
    assert "PermissionError" in run.stderr


def test_a_hard_link_into_the_folder_is_refused(home, tmp_path):
    link = home / "work" / "hl.txt"
    os.link(home / ".keys" / "id_decoy", link)
    run = _run(f"print(open({str(link)!r}).read())", home, tmp_path)
    assert "SECRET-BYTES" not in run.stdout
    assert "PermissionError" in run.stderr


@pytest.mark.parametrize(
    "move_expr",
    [
        "os.rename(os.path.join(HOME, '.keys', 'id_decoy'), 'out.txt')",
        "os.replace(os.path.join(HOME, '.keys', 'id_decoy'), 'out.txt')",
        "shutil.move(os.path.join(HOME, '.keys', 'id_decoy'), 'out.txt')",
        "os.rename(os.path.join(HOME, '.keys'), 'out')",
        "os.link(os.path.join(HOME, '.keys', 'id_decoy'), 'out.txt')",
        "shutil.copyfile(os.path.join(HOME, '.keys', 'id_decoy'), 'out.txt')",
        "shutil.copy2(os.path.join(HOME, '.keys', 'id_decoy'), 'out.txt')",
        "shutil.copytree(os.path.join(HOME, '.keys'), 'out')",
    ],
)
def test_moving_a_file_out_of_the_folder_is_refused(home, tmp_path, move_expr):
    code = f"""
        import os, shutil
        HOME = {str(home)!r}
        {move_expr}
        print(open('out.txt').read() if os.path.isfile('out.txt') else 'moved-dir')
        """
    run = _run(code, home, tmp_path)
    assert run.returncode == 1
    assert "PermissionError" in run.stderr
    assert (home / ".keys" / "id_decoy").read_text() == "SECRET-BYTES"
    assert not (home / "work" / "out.txt").exists()


@pytest.mark.parametrize(
    "change_expr",
    [
        "os.rename('notes.txt', os.path.join(HOME, '.keys', 'planted'))",
        "os.replace('notes.txt', os.path.join(HOME, '.keys', 'id_decoy'))",
        "shutil.copyfile('notes.txt', os.path.join(HOME, '.keys', 'id_decoy'))",
        "os.link('notes.txt', os.path.join(HOME, '.keys', 'planted'))",
        "os.symlink(os.path.abspath('notes.txt'), os.path.join(HOME, '.keys', 'planted'))",
        "os.remove(os.path.join(HOME, '.keys', 'id_decoy'))",
        "os.unlink(os.path.join(HOME, '.keys', 'id_decoy'))",
        "shutil.rmtree(os.path.join(HOME, '.keys'))",
        "os.truncate(os.path.join(HOME, '.keys', 'id_decoy'), 0)",
    ],
)
def test_changing_what_the_folder_holds_is_refused(home, tmp_path, change_expr):
    code = f"""
        import os, shutil
        HOME = {str(home)!r}
        {change_expr}
        """
    run = _run(code, home, tmp_path)
    assert run.returncode == 1
    assert "PermissionError" in run.stderr
    assert sorted(p.name for p in (home / ".keys").iterdir()) == ["id_decoy"]
    assert (home / ".keys" / "id_decoy").read_text() == "SECRET-BYTES"
    assert (home / "work" / "notes.txt").read_text() == "plain"


def test_copying_a_hard_link_into_the_folder_is_refused(home, tmp_path):
    link = home / "work" / "hl.txt"
    os.link(home / ".keys" / "id_decoy", link)
    code = f"""
        import shutil
        shutil.copy({str(link)!r}, 'out.txt')
        print(open('out.txt').read())
        """
    run = _run(code, home, tmp_path)
    assert "SECRET-BYTES" not in run.stdout
    assert "PermissionError" in run.stderr
    assert not (home / "work" / "out.txt").exists()


def test_other_files_move_copy_and_delete_as_usual(home, tmp_path):
    code = """
        import os, shutil
        shutil.copyfile('notes.txt', 'copy.txt')
        os.rename('notes.txt', 'renamed.txt')
        os.remove('copy.txt')
        """
    run = _run(code, home, tmp_path)
    assert run.returncode == 0, run.stderr
    assert (home / "work" / "renamed.txt").read_text() == "plain"
    assert not (home / "work" / "copy.txt").exists()


def test_other_files_open_as_usual(home, tmp_path):
    code = f"""
        import os
        print(open('notes.txt').read())
        print(sorted(os.listdir({str(home / '.keys')!r})))
        """
    run = _run(code, home, tmp_path)
    assert run.returncode == 0, run.stderr
    assert "plain" in run.stdout


def test_no_folders_named_means_no_guard(home):
    assert guarded_open_refusal(str(home / ".keys" / "id_decoy"), []) is None


def test_the_script_runs_as_python_would(home, tmp_path):
    code = """
        import os, sys
        print(__name__, os.path.basename(sys.argv[0]), sys.path[0] == os.path.dirname(os.path.abspath(__file__)))
        """
    run = _run(code, home, tmp_path)
    assert run.returncode == 0, run.stderr
    assert run.stdout.split() == ["__main__", "snippet.py", "True"]


class TestFileToolsJudgeTheFile:
    @pytest.fixture
    def validator(self, home, monkeypatch):
        monkeypatch.setattr(security, "SECRET_DIRECTORIES", {str(home / ".keys")})
        return PathValidator(allowed_paths=[str(home / "work")])

    def test_a_hard_link_read_is_refused(self, home, validator):
        link = home / "work" / "hl.txt"
        os.link(home / ".keys" / "id_decoy", link)
        blocked, reason = validator.is_read_blocked(str(link))
        assert blocked
        assert "credentials" in reason

    def test_a_hard_link_write_is_refused(self, home, validator):
        link = home / "work" / "hl.txt"
        os.link(home / ".keys" / "id_decoy", link)
        assert validator.is_write_blocked(str(link))[0]

    def test_an_ordinary_file_with_two_names_is_readable(self, home, validator):
        os.link(home / "work" / "notes.txt", home / "work" / "copy.txt")
        assert validator.is_read_blocked(str(home / "work" / "copy.txt")) == (
            False,
            "",
        )

    def test_a_large_credential_folder_refuses_only_its_own_files(
        self, home, validator
    ):
        for i in range(300):
            (home / ".keys" / f"cache_{i}").write_text("x")
        os.link(home / "work" / "notes.txt", home / "work" / "copy.txt")
        assert not validator.is_read_blocked(str(home / "work" / "copy.txt"))[0]
        os.link(home / ".keys" / "id_decoy", home / "work" / "hl.txt")
        assert validator.is_read_blocked(str(home / "work" / "hl.txt"))[0]

    def test_a_link_made_after_a_check_is_still_caught(self, home, validator):
        assert not validator.is_read_blocked(str(home / "work" / "notes.txt"))[0]
        os.link(home / ".keys" / "id_decoy", home / "work" / "late.txt")
        assert validator.is_read_blocked(str(home / "work" / "late.txt"))[0]


def test_the_open_guard_reads_the_folders_once(home, monkeypatch):
    from gaiapy import python_runner

    calls = []
    real = python_runner._file_ids
    monkeypatch.setattr(
        python_runner, "_file_ids", lambda f: calls.append(f) or real(f)
    )
    ids = python_runner._file_ids([str(home / ".keys")])
    calls.clear()
    os.link(home / ".keys" / "id_decoy", home / "work" / "hl.txt")
    for _ in range(5):
        assert python_runner.guarded_open_refusal(
            str(home / "work" / "hl.txt"), [str(home / ".keys")], ids
        )
    assert calls == []
