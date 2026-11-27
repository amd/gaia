# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The programs skills use: found where they are, fetched only from their publisher.

GAIA reported "`git` is not installed" on a PC with Git for Windows in Program
Files, because it only looked on ``PATH``. These tests pin the lookup order,
that a download is unpacked only when its checksum matches the pin, the exact
package-manager argv, and which programs setup installs by default: Git only
when none is found, Tesseract yes, ffmpeg only when named, LibreOffice never
unless named.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gaia import program_lookup, programs
from gaia.config import GaiaConfig
from gaia.installer import init_command
from gaia.installer.init_command import InitCommand

REAL_PROGRAMS = dict(programs.PROGRAMS)


def _zip(members: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


MINGIT = _zip({"cmd/git.exe": b"MZ fake git", "LICENSE.txt": b"GPL-2.0"})


@pytest.fixture
def windows(monkeypatch, tmp_path):
    """A Windows PC with nothing installed, and a home folder under tmp_path."""
    monkeypatch.setattr(programs, "PROGRAMS", dict(REAL_PROGRAMS))
    monkeypatch.setattr(
        programs, "BY_COMMAND", {p.command: p.id for p in REAL_PROGRAMS.values()}
    )
    monkeypatch.setattr(programs.sys, "platform", "win32")
    monkeypatch.setattr(programs.platform, "system", lambda: "Windows")
    monkeypatch.setattr(programs.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(programs.shutil, "which", lambda name, path=None: None)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.setenv(variable, str(tmp_path / variable))
    monkeypatch.setattr(
        program_lookup, "_registry_path_entries", lambda: [], raising=True
    )
    return tmp_path


def _pin_git(monkeypatch, data: bytes) -> None:
    """Pin the registry's Git download to *data*'s checksum."""
    git = programs.PROGRAMS["git"]
    key = ("win32", "amd64")
    pinned = replace(git.downloads[key], sha256=hashlib.sha256(data).hexdigest())
    monkeypatch.setitem(programs.PROGRAMS, "git", replace(git, downloads={key: pinned}))


def _serve(data: bytes):
    fetched = []

    def fetch(url, target):
        fetched.append(url)
        Path(target).write_bytes(data)

    return fetch, fetched


class TestRegistry:
    def test_every_download_is_pinned_to_an_official_release(self):
        for program in REAL_PROGRAMS.values():
            for download in program.downloads.values():
                assert download.url.startswith(
                    "https://github.com/git-for-windows/git/releases/download/"
                )
                assert len(download.sha256) == 64
                assert download.version in download.url

    def test_setup_defaults(self):
        defaults = {p.id: p.setup for p in REAL_PROGRAMS.values()}
        assert defaults == {
            "git": programs.PRESELECTED,
            "tesseract": programs.PRESELECTED,
            "ffmpeg": programs.OFFERED,
            "libreoffice": programs.EXPLICIT,
        }

    def test_every_program_names_its_licence_and_source(self):
        for program in REAL_PROGRAMS.values():
            assert program.licence and program.source.startswith("https://")


class TestDetection:
    def test_git_in_program_files_but_off_path_is_found(self, windows):
        folder = windows / "ProgramFiles" / "Git" / "cmd"
        folder.mkdir(parents=True)
        (folder / "git.exe").write_bytes(b"MZ")
        found = programs.find("git")
        assert found == programs.Found(str(folder / "git.exe"), "standard")

    def test_path_wins_over_a_standard_folder(self, windows, monkeypatch):
        monkeypatch.setattr(
            programs.shutil, "which", lambda name, path=None: r"C:\tools\git.exe"
        )
        assert programs.find("git") == programs.Found(r"C:\tools\git.exe", "path")

    def test_nothing_installed_is_none(self, windows):
        assert programs.find("git") is None
        assert programs.find_command("soffice") is None

    @pytest.mark.real_path_refresh
    def test_a_found_program_joins_gaias_own_path(self, windows, monkeypatch):
        folder = windows / "ProgramFiles" / "Git" / "cmd"
        folder.mkdir(parents=True)
        (folder / "git.exe").write_bytes(b"MZ")
        monkeypatch.setattr(program_lookup.sys, "platform", "win32")
        monkeypatch.setenv("PATH", str(windows))
        assert program_lookup.refresh_path() == [str(folder)]
        assert os.environ["PATH"].split(os.pathsep) == [str(windows), str(folder)]

    def test_readiness_lists_found_and_missing(self, windows):
        folder = windows / "ProgramFiles" / "Git" / "cmd"
        folder.mkdir(parents=True)
        (folder / "git.exe").write_bytes(b"MZ")
        with patch.object(programs, "version", lambda program_id: "git version 2.56.1"):
            lines = [programs.status_line(row) for row in programs.status()]
        assert lines[0].startswith("[ok] Git: found at ")
        assert "git version 2.56.1" in lines[0]
        assert "Tesseract OCR: missing (optional; about 50 MB)" in lines[1]
        assert "LibreOffice: missing (optional, large; about 350 MB)" in lines[3]
        assert "--with-program libreoffice" in lines[3]


class TestDownload:
    def test_a_matching_download_is_unpacked_under_gaias_folder(
        self, windows, monkeypatch
    ):
        _pin_git(monkeypatch, MINGIT)
        fetch, fetched = _serve(MINGIT)
        found = programs.install("git", fetch)
        expected = windows / "home" / ".gaia" / "programs" / "git" / "2.56.1" / "cmd"
        assert found == programs.Found(str(expected / "git.exe"), "gaia")
        assert fetched == [programs.PROGRAMS["git"].downloads[("win32", "amd64")].url]

    def test_a_checksum_mismatch_is_refused_and_nothing_is_unpacked(
        self, windows, monkeypatch
    ):
        _pin_git(monkeypatch, MINGIT)
        tampered = _zip({"cmd/git.exe": b"MZ something else"})
        fetch, _ = _serve(tampered)
        with pytest.raises(programs.ProgramError, match="pinned checksum"):
            programs.install("git", fetch)
        assert not (windows / "home" / ".gaia" / "programs").exists()

    def test_an_archive_without_the_program_is_refused(self, windows, monkeypatch):
        empty = _zip({"README.txt": b"nothing here"})
        _pin_git(monkeypatch, empty)
        fetch, _ = _serve(empty)
        with pytest.raises(programs.ProgramError, match="holds no"):
            programs.install("git", fetch)

    def test_a_failed_download_says_how_to_retry(self, windows):
        def offline(url, target):
            raise OSError("no route to host")

        with pytest.raises(programs.ProgramError) as raised:
            programs.install("git", offline)
        assert "no route to host" in str(raised.value)
        assert "gaia init --programs --with-program git" in str(raised.value)

    def test_an_installed_program_is_never_downloaded_again(self, windows, monkeypatch):
        monkeypatch.setattr(
            programs.shutil, "which", lambda name, path=None: r"C:\tools\git.exe"
        )
        fetch, fetched = _serve(MINGIT)
        assert programs.install("git", fetch).origin == "path"
        assert fetched == []

    def test_no_download_off_windows(self, windows, monkeypatch):
        monkeypatch.setattr(programs.sys, "platform", "linux")
        monkeypatch.setattr(programs.platform, "system", lambda: "Linux")
        assert programs.how_to_install("git") == "sudo apt install git"
        with pytest.raises(programs.ProgramError, match="sudo apt install git"):
            programs.install("git", _serve(MINGIT)[0])


class TestPackageManager:
    def _run(self, windows, monkeypatch, returncode=0, installs=None):
        calls = []

        def which(name, path=None):
            if name == "winget":
                return r"C:\winget.exe"
            return None

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            if installs:
                installs()
            return SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

        monkeypatch.setattr(programs.shutil, "which", which)
        monkeypatch.setattr(programs.subprocess, "run", fake_run)
        return calls

    def test_tesseract_runs_the_publishers_winget_package(self, windows, monkeypatch):
        folder = windows / "ProgramFiles" / "Tesseract-OCR"

        def installs():
            folder.mkdir(parents=True)
            (folder / "tesseract.exe").write_bytes(b"MZ")

        calls = self._run(windows, monkeypatch, installs=installs)
        found = programs.install("tesseract")
        assert calls[0][0] == [
            r"C:\winget.exe",
            "install",
            "--id",
            "UB-Mannheim.TesseractOCR",
            "--exact",
            "--source",
            "winget",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        assert calls[0][1]["stdin"] == programs.subprocess.DEVNULL
        assert found.origin == "standard"

    def test_a_failed_install_gives_the_command_to_run_by_hand(
        self, windows, monkeypatch
    ):
        self._run(windows, monkeypatch, returncode=1)
        with pytest.raises(programs.ProgramError) as raised:
            programs.install("tesseract")
        assert "winget install --id UB-Mannheim.TesseractOCR" in str(raised.value)

    def test_success_without_the_program_is_a_failure(self, windows, monkeypatch):
        self._run(windows, monkeypatch)
        with pytest.raises(programs.ProgramError, match="still not found"):
            programs.install("tesseract")

    def test_ffmpeg_uses_the_one_existing_bootstrap(self, windows):
        with patch("gaia.audio.media.ensure_ffmpeg", side_effect=lambda: "x") as ensure:
            with pytest.raises(programs.ProgramError, match="still not found"):
                programs.install("ffmpeg")
        ensure.assert_called_once_with()


class TestSetup:
    def _offer(self, monkeypatch, answers=None, **kwargs):
        installed = []

        def install(program_id, fetch=None):
            installed.append(program_id)
            return programs.Found(f"/gaia/{program_id}", "gaia")

        monkeypatch.setattr(programs, "install", install)
        cmd = InitCommand(profile="gaia", **kwargs)
        printed = []
        cmd._print = lambda msg, end="\n": printed.append(msg)
        replies = iter(answers or [])
        with (
            patch.object(init_command, "RICH_AVAILABLE", False),
            patch("builtins.input", lambda *_: next(replies)),
        ):
            ok = cmd.offer_programs()
        return installed, printed, cmd, ok

    def test_unattended_setup_installs_git_and_tesseract_only(
        self, windows, monkeypatch
    ):
        installed, _, cmd, ok = self._offer(monkeypatch, yes=True)
        assert installed == ["git", "tesseract"]
        assert ok and cmd._program_notes == []

    def test_git_is_left_alone_when_one_is_found(self, windows, monkeypatch):
        folder = windows / "ProgramFiles" / "Git" / "cmd"
        folder.mkdir(parents=True)
        (folder / "git.exe").write_bytes(b"MZ")
        installed, printed, _, _ = self._offer(monkeypatch, yes=True)
        assert installed == ["tesseract"]
        assert any("Git: found at" in line for line in printed)

    def test_ffmpeg_and_libreoffice_need_naming(self, windows, monkeypatch):
        installed, _, _, _ = self._offer(
            monkeypatch, yes=True, with_programs=["ffmpeg", "libreoffice"]
        )
        assert installed == ["git", "tesseract", "ffmpeg", "libreoffice"]

    def test_the_prompt_never_mentions_libreoffice(self, windows, monkeypatch):
        installed, printed, _, _ = self._offer(
            monkeypatch, answers=["", "", ""], yes=False
        )
        text = "\n".join(printed)
        assert installed == ["git", "tesseract"]  # Enter: yes, yes, and no to ffmpeg
        assert "LibreOffice" not in text
        assert "GPL-2.0-only" in text and "Apache-2.0" in text
        assert "40 MB download" in text and "github.com/git-for-windows" in text

    def test_ffmpeg_is_installed_on_an_explicit_yes(self, windows, monkeypatch):
        installed, _, _, _ = self._offer(
            monkeypatch, answers=["n", "n", "y"], yes=False
        )
        assert installed == ["ffmpeg"]

    def test_an_opt_out_is_remembered(self, windows, monkeypatch):
        installed, _, _, _ = self._offer(
            monkeypatch, yes=True, without_programs=["tesseract"]
        )
        assert installed == ["git"]
        assert GaiaConfig.load().skipped_programs == ["tesseract"]
        again, _, _, _ = self._offer(monkeypatch, yes=True)
        assert again == ["git"]

    def test_a_failed_install_is_reported_and_setup_carries_on(
        self, windows, monkeypatch
    ):
        def install(program_id, fetch=None):
            if program_id == "git":
                raise programs.ProgramError("offline")
            return programs.Found("/gaia/x", "gaia")

        monkeypatch.setattr(programs, "install", install)
        cmd = InitCommand(profile="gaia", yes=True)
        cmd._print = lambda msg, end="\n": None
        with patch.object(init_command, "RICH_AVAILABLE", False):
            assert cmd.offer_programs() is False
        assert cmd._program_notes == ["Git was NOT installed. offline"]

    def test_linux_names_the_command_instead_of_installing(self, windows, monkeypatch):
        monkeypatch.setattr(programs.sys, "platform", "linux")
        monkeypatch.setattr(programs.platform, "system", lambda: "Linux")
        installed, _, cmd, _ = self._offer(monkeypatch, yes=True)
        assert installed == []
        assert "sudo apt install git" in cmd._program_notes[0]


class TestCli:
    def _main(self, monkeypatch, *argv):
        from gaia import cli

        monkeypatch.setattr(sys, "argv", ["gaia", "init", *argv])
        with pytest.raises(SystemExit) as raised:
            cli.main()
        return raised.value.code

    def test_programs_runs_only_this_step(self, monkeypatch):
        seen = {}

        def run_programs(**kwargs):
            seen.update(kwargs)
            return 0

        monkeypatch.setattr(init_command, "run_programs", run_programs)
        monkeypatch.setattr(
            init_command, "run_init", lambda **kw: pytest.fail("full init ran")
        )
        code = self._main(
            monkeypatch, "--programs", "--with-program", "libreoffice", "--yes"
        )
        assert code == 0
        assert seen["with_programs"] == ["libreoffice"] and seen["yes"] is True

    def test_check_lists_and_exits_nonzero_when_a_default_is_missing(
        self, windows, capsys
    ):
        assert init_command.run_programs(check=True) == 1
        out = capsys.readouterr().out
        assert "[  ] Git: missing" in out and "LibreOffice: missing" in out

    def test_an_unknown_program_is_refused(self, monkeypatch):
        assert self._main(monkeypatch, "--programs", "--with-program", "nmap") == 2


class TestSkillLoad:
    def test_a_skill_that_declares_a_missing_program_says_so(self, windows, tmp_path):
        pytest.importorskip("gaia_agent")
        from gaia.agents.base.agent import Agent
        from gaia.agents.base.tools import _TOOL_REGISTRY
        from gaia.agents.tools.skill_library_tools import SkillLibraryToolsMixin

        from .skills_helpers import isolated_manager, write_skill_dir

        class LibraryAgent(Agent, SkillLibraryToolsMixin):
            def _get_system_prompt(self):
                return "Use tools."

            def _register_tools(self):
                self.register_skill_library_tools()

        root = tmp_path / "bundled"
        write_skill_dir(
            root,
            "sheets",
            "---\nname: sheets\ndescription: A test skill. Use when testing.\n"
            "metadata:\n  gaia:\n    security_tier: verified\n    permissions:\n"
            "      - program:run:soffice\n---\n\n# sheets\n\nBody.\n",
        )
        saved = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            with patch("gaia.agents.base.agent.AgentSDK"):
                agent = LibraryAgent(silent_mode=True, skip_lemonade=True)
            agent._skill_manager = isolated_manager(tmp_path, agent_skill_dirs=[root])
            payload = agent.activate_skill("sheets")
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)
        assert payload["status"] == "success"
        assert payload["missing_programs"] == ["libreoffice"]
        assert "about 350 MB" in payload["warning"]
        assert "--with-program libreoffice" in payload["warning"]
