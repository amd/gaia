# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The optional Python libraries ``gaia init`` downloads for scripts and skills.

A source install ran the model's scripts in a Python with no pandas, so the
first data script failed and the model fell back to hand-written csv code.
``gaia init`` now offers one pinned set. These tests pin where the versions
come from, the argv the installer is run with, that a skill cannot smuggle an
installer option in, that a "no" is remembered, and that a failed or partial
install is reported and never called success.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from packaging.requirements import Requirement

from gaia.agents import install_hints
from gaia.config import GaiaConfig
from gaia.installer import data_libraries, init_command
from gaia.installer.data_libraries import DataLibrariesError, LibraryStatus
from gaia.installer.init_command import InitCommand

REPO = Path(__file__).resolve().parents[3]
PINS = ["pandas==2.3.3", "openpyxl==3.1.5"]


def _setup_kwargs() -> dict:
    tree = ast.parse((REPO / "setup.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setup":
            return {
                kw.arg: ast.literal_eval(kw.value)
                for kw in node.keywords
                if kw.arg in ("install_requires", "extras_require")
            }
    raise AssertionError("No setup(...) call found in setup.py")


def _extra() -> list:
    return _setup_kwargs()["extras_require"][data_libraries.EXTRA]


def _pins() -> dict:
    return {
        Requirement(r).name: next(iter(Requirement(r).specifier)).version
        for r in _extra()
    }


@pytest.fixture
def env(monkeypatch):
    """A Python with pip, GAIA declaring ``PINS``, and a settable installed set."""
    installed = {}

    # Not importlib.metadata.version itself: every import in the process uses it.
    monkeypatch.setattr(data_libraries, "_installed_version", installed.get)
    monkeypatch.setattr(
        data_libraries, "gaia_extra_requirements", lambda extras: list(PINS)
    )
    monkeypatch.setattr(data_libraries, "bundled_skill_roots", lambda: [])
    monkeypatch.setattr(install_hints, "_pip_available", lambda: True)
    monkeypatch.setattr(data_libraries, "is_frozen", lambda: False)
    return installed


def _skill(root: Path, name: str, dependencies: list) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    listed = "".join(f"        - {json.dumps(d)}\n" for d in dependencies)
    folder.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill.\nmetadata:\n  gaia:\n"
        f"    requirements:\n      dependencies:\n{listed}---\n\nBody.\n",
        encoding="utf-8",
    )


def _run_install(installs=None, returncode=0, stderr="", raises=None):
    """Run ``install()`` with a fake installer that adds *installs* to *env*."""
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if raises:
            raise raises
        if installs:
            installs()
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    with patch.object(data_libraries.subprocess, "run", side_effect=fake_run):
        return data_libraries.install(), calls


class TestPinsHaveOneSource:
    def test_the_extra_is_exact_pins(self):
        for raw in _extra():
            requirement = Requirement(raw)
            assert requirement.marker is None, raw
            assert [s.operator for s in requirement.specifier] == ["=="], raw

    def test_the_extra_holds_the_recommended_set(self):
        assert set(_pins()) == {
            "pandas",
            "numpy",
            "matplotlib",
            "openpyxl",
            "xlsxwriter",
            "python-docx",
            "python-pptx",
            "pypdf",
            "pypdfium2",
            "reportlab",
            "duckdb",
            "pyarrow",
        }

    def test_the_helpers_scripts_rely_on_are_core_requirements(self):
        """Always installed with GAIA, so the optional step need not carry them."""
        core = {Requirement(r).name for r in _setup_kwargs()["install_requires"]}
        assert {"pillow", "python-dateutil", "charset-normalizer", "numpy"} <= core

    def test_every_pin_satisfies_gaias_other_requirements(self):
        """[ui] and the pins share one Python; they must not conflict."""
        kwargs = _setup_kwargs()
        pins = _pins()
        others = list(kwargs["install_requires"])
        for extra, requirements in kwargs["extras_require"].items():
            if extra != data_libraries.EXTRA:
                others += requirements
        for raw in others:
            requirement = Requirement(raw)
            pin = pins.get(requirement.name.lower())
            if pin:
                assert requirement.specifier.contains(pin), f"{raw} excludes {pin}"

    def test_every_pin_satisfies_the_hub_skills_that_declare_it(self):
        pins = _pins()
        declared = data_libraries.skill_dependencies([str(REPO / "hub" / "skills")])
        assert "office-docs" in declared
        for skill, specs in declared.items():
            for raw in specs:
                requirement = Requirement(raw)
                pin = pins.get(requirement.name.lower())
                if pin:
                    assert requirement.specifier.contains(pin), f"{skill}: {raw}"

    def test_pins_are_read_from_the_installed_extra(self, monkeypatch):
        asked = []

        def requirements(extras):
            asked.append(list(extras))
            return list(PINS)

        monkeypatch.setattr(data_libraries, "gaia_extra_requirements", requirements)
        assert data_libraries.pinned_libraries() == {
            "pandas": "2.3.3",
            "openpyxl": "3.1.5",
        }
        assert asked == [["data-libraries"]]

    def test_a_loose_requirement_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            data_libraries, "gaia_extra_requirements", lambda extras: ["pandas>=2"]
        )
        with pytest.raises(RuntimeError, match="not an exact"):
            data_libraries.pinned_libraries()

    def test_an_install_without_the_extra_says_how_to_get_it(self, monkeypatch):
        def no_extra(extras):
            raise RuntimeError("amd-gaia declares no extra ['data-libraries'].")

        monkeypatch.setattr(data_libraries, "gaia_extra_requirements", no_extra)
        with pytest.raises(RuntimeError, match="pip install -e"):
            data_libraries.pinned_libraries()


class TestSkillDependencies:
    def test_setup_installs_the_union_with_gaias_pin_winning(self, env, tmp_path):
        _skill(tmp_path, "decks", ["openpyxl>=3.1,<4", "lxml>=5.0"])
        declared = data_libraries.skill_dependencies([str(tmp_path)])
        status = data_libraries.library_status(declared)
        assert [(s.name, s.requirement, s.skills) for s in status] == [
            ("pandas", "pandas==2.3.3", ()),
            ("openpyxl", "openpyxl==3.1.5", ("decks",)),
            ("lxml", "lxml>=5.0", ("decks",)),
        ]

    def test_a_skills_own_library_is_installed_as_declared(
        self, env, tmp_path, monkeypatch
    ):
        _skill(tmp_path, "decks", ["lxml>=5.0"])
        monkeypatch.setattr(
            data_libraries, "bundled_skill_roots", lambda: [str(tmp_path)]
        )
        env.update(pandas="2.3.3", openpyxl="3.1.5")
        status, calls = _run_install(installs=lambda: env.update(lxml="6.0.0"))
        assert calls[0][0] == [sys.executable, "-m", "pip", "install", "lxml>=5.0"]
        assert all(s.ok for s in status)

    @pytest.mark.parametrize(
        "spec",
        [
            "https://evil.example/pkg.whl",
            "pkg @ https://evil.example/pkg.whl",
            "git+https://github.com/x/y",
            "--index-url https://evil.example/simple",
            "pkg --index-url https://evil.example/simple",
            "-e .",
            "../local",
            "pkg[extra]>=1",
            "pkg>=1; python_version < '3.12'",
            "pkg>=1 other",
            "",
        ],
    )
    def test_anything_but_a_name_and_versions_is_refused(self, tmp_path, spec):
        _skill(tmp_path, "sneaky", [spec])
        with pytest.raises(ValueError, match="not a plain package requirement"):
            data_libraries.skill_dependencies([str(tmp_path)])

    def test_plain_requirements_are_accepted(self):
        assert data_libraries.plain_requirement("openpyxl >= 3.1, < 4") == (
            "openpyxl",
            ">=3.1,<4",
        )
        assert data_libraries.plain_requirement("lxml") == ("lxml", "")

    def test_per_skill_readiness_names_what_is_missing(self, env, tmp_path, capsys):
        _skill(tmp_path, "decks", ["openpyxl>=3.1", "lxml>=5.0"])
        _skill(tmp_path, "sheets", ["openpyxl>=3.1"])
        env.update(pandas="2.3.3", openpyxl="3.1.5")
        with patch.object(
            data_libraries, "bundled_skill_roots", lambda: [str(tmp_path)]
        ):
            assert data_libraries.run_command(check=True) == 1
        out = capsys.readouterr().out
        assert "[  ] skill decks: needs lxml" in out
        assert "[ok] skill sheets: libraries installed" in out


class TestInstall:
    def test_nothing_runs_when_every_pin_is_installed(self, env):
        env.update(pandas="2.3.3", openpyxl="3.1.5")
        status, calls = _run_install()
        assert calls == []
        assert all(s.ok for s in status)

    def test_only_what_is_missing_is_installed_at_its_pin(self, env):
        env.update(openpyxl="3.1.5")
        status, calls = _run_install(installs=lambda: env.update(pandas="2.3.3"))
        assert [argv for argv, _ in calls] == [
            [sys.executable, "-m", "pip", "install", "pandas==2.3.3"]
        ]
        assert calls[0][1]["timeout"] == data_libraries.INSTALL_TIMEOUT_S
        assert all(s.ok for s in status)

    def test_another_version_is_replaced_by_the_pin(self, env):
        env.update(pandas="2.2.0", openpyxl="3.1.5")
        _, calls = _run_install(installs=lambda: env.update(pandas="2.3.3"))
        assert calls[0][0][-1] == "pandas==2.3.3"

    def test_installer_venv_without_pip_uses_uv_on_this_interpreter(
        self, env, monkeypatch
    ):
        monkeypatch.setattr(install_hints, "_pip_available", lambda: False)
        monkeypatch.setattr(
            install_hints.shutil, "which", lambda n: "/bin/uv" if n == "uv" else None
        )
        _, calls = _run_install(
            installs=lambda: env.update(pandas="2.3.3", openpyxl="3.1.5")
        )
        assert calls[0][0] == [
            "/bin/uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            *PINS,
        ]

    def test_a_failed_install_names_what_is_missing_and_how_to_retry(self, env):
        with pytest.raises(DataLibrariesError) as raised:
            _run_install(returncode=1, stderr="error: no route to pypi.org\n")
        message = str(raised.value)
        assert "pandas, openpyxl" in message
        assert "no route to pypi.org" in message
        assert "gaia init --data-libraries" in message
        assert "pip install pandas==2.3.3 openpyxl==3.1.5" in message

    def test_installer_success_with_a_library_still_missing_is_a_failure(self, env):
        with pytest.raises(DataLibrariesError, match="still lacks: openpyxl"):
            _run_install(installs=lambda: env.update(pandas="2.3.3"))

    def test_a_timeout_is_a_failure(self, env):
        with pytest.raises(DataLibrariesError, match="timed out"):
            _run_install(raises=subprocess.TimeoutExpired("pip", 900))

    def test_no_installer_is_a_failure(self, env, monkeypatch):
        monkeypatch.setattr(install_hints, "_pip_available", lambda: False)
        monkeypatch.setattr(install_hints, "_find_uv", lambda: None)
        with pytest.raises(DataLibrariesError, match="Install uv"):
            _run_install()


class TestReadiness:
    def test_each_library_is_listed_with_its_version_or_as_not_installed(
        self, env, capsys
    ):
        env.update(pandas="2.3.3")
        assert data_libraries.run_command(check=True) == 1
        out, err = capsys.readouterr()
        assert "[ok] pandas 2.3.3" in out
        assert "[  ] openpyxl: not installed (optional, recommended; 3.1.5)" in out
        assert "gaia init --data-libraries" in err

    def test_check_installs_nothing_and_saves_nothing(self, env):
        data_libraries.remember_choice(False)
        with patch.object(data_libraries.subprocess, "run") as run:
            data_libraries.run_command(check=True)
        run.assert_not_called()
        assert GaiaConfig.load().data_libraries is False

    def test_ready_exits_zero(self, env, capsys):
        env.update(pandas="2.3.3", openpyxl="3.1.5")
        assert data_libraries.run_command(check=True) == 0
        assert "READY" in capsys.readouterr().out

    def test_json_lists_every_library(self, env, capsys):
        env.update(pandas="2.2.0")
        assert data_libraries.run_command(check=True, as_json=True) == 1
        report = json.loads(capsys.readouterr().out)
        assert report["ready"] is False
        assert report["libraries"] == [
            {
                "name": "pandas",
                "requirement": "pandas==2.3.3",
                "installed": "2.2.0",
                "ok": False,
            },
            {
                "name": "openpyxl",
                "requirement": "openpyxl==3.1.5",
                "installed": None,
                "ok": False,
            },
        ]

    def test_the_command_reports_a_failed_install_and_exits_nonzero(self, env, capsys):
        fake = SimpleNamespace(returncode=1, stdout="", stderr="offline")
        with patch.object(data_libraries.subprocess, "run", return_value=fake):
            assert data_libraries.run_command() == 1
        out, err = capsys.readouterr()
        assert "[  ] pandas: not installed" in out
        assert "offline" in err and "READY" not in out

    def test_asking_for_them_undoes_an_earlier_no(self, env):
        data_libraries.remember_choice(False)
        assert data_libraries.declined()
        env.update(pandas="2.3.3", openpyxl="3.1.5")
        assert data_libraries.run_command() == 0
        assert not data_libraries.declined()

    def test_a_frozen_build_has_nothing_to_install(self, env, monkeypatch, capsys):
        monkeypatch.setattr(data_libraries, "is_frozen", lambda: True)
        with patch.object(data_libraries.subprocess, "run") as run:
            assert data_libraries.run_command() == 0
        run.assert_not_called()
        assert "bundles" in capsys.readouterr().out

    def test_summary_is_none_when_nothing_is_missing(self):
        status = [LibraryStatus("pandas", "pandas==2.3.3", "2.3.3", "2.3.3")]
        assert data_libraries.missing_summary(status) is None


class TestInitStep:
    def _cmd(self, yes=True, **kwargs):
        cmd = InitCommand(profile="gaia", yes=yes, **kwargs)
        printed = []
        cmd._print = lambda msg, end="\n": printed.append(msg)
        return cmd, printed

    def _offer(self, cmd, answer="", installs=None, returncode=0, stderr=""):
        def fake_run(argv, **kwargs):
            if installs:
                installs()
            return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

        with (
            patch.object(init_command, "RICH_AVAILABLE", False),
            patch.object(data_libraries.subprocess, "run", side_effect=fake_run) as ran,
            patch("builtins.input", return_value=answer),
        ):
            cmd._offer_data_libraries()
        return ran

    def test_unattended_setup_installs_without_asking(self, env):
        cmd, printed = self._cmd(yes=True)
        ran = self._offer(
            cmd, installs=lambda: env.update(pandas="2.3.3", openpyxl="3.1.5")
        )
        assert ran.call_count == 1
        assert cmd._data_libraries_note is None
        assert "pandas 2.3.3" in "\n".join(printed)

    def test_the_prompt_states_size_source_and_purpose_and_defaults_to_yes(self, env):
        cmd, printed = self._cmd(yes=False)
        ran = self._offer(
            cmd, installs=lambda: env.update(pandas="2.3.3", openpyxl="3.1.5")
        )
        offer = "\n".join(printed)
        assert f"about {data_libraries.DOWNLOAD_MB} MB" in offer
        assert "PyPI" in offer and "spreadsheets" in offer
        assert ran.call_count == 1

    def test_a_no_is_remembered_and_setup_carries_on(self, env):
        cmd, _ = self._cmd(yes=False)
        assert self._offer(cmd, answer="n").call_count == 0
        assert data_libraries.declined()
        assert "gaia init --data-libraries" in cmd._data_libraries_note

        again, _ = self._cmd(yes=True)
        assert self._offer(again).call_count == 0
        assert "as you chose" in again._data_libraries_note

    def test_the_opt_out_flag_skips_and_saves(self, env):
        cmd, _ = self._cmd(yes=True, no_data_libraries=True)
        assert self._offer(cmd).call_count == 0
        assert GaiaConfig.load().data_libraries is False

    def test_a_failed_download_is_reported_but_does_not_raise(self, env):
        cmd, printed = self._cmd(yes=True)
        self._offer(cmd, returncode=1, stderr="offline")
        assert "NOT installed" in cmd._data_libraries_note
        assert "offline" in cmd._data_libraries_note
        assert "gaia init --data-libraries" in "\n".join(printed)

    def test_nothing_is_asked_when_everything_is_installed(self, env):
        env.update(pandas="2.3.3", openpyxl="3.1.5")
        cmd, _ = self._cmd(yes=False)
        with (
            patch.object(init_command, "RICH_AVAILABLE", False),
            patch("builtins.input", side_effect=AssertionError("asked")),
        ):
            cmd._offer_data_libraries()
        assert cmd._data_libraries_note is None


class TestCli:
    def _main(self, monkeypatch, *argv):
        from gaia import cli

        monkeypatch.setattr(sys, "argv", ["gaia", "init", *argv])
        with pytest.raises(SystemExit) as raised:
            cli.main()
        return raised.value.code

    def test_the_flag_runs_only_this_step(self, monkeypatch):
        seen = {}

        def run_command(check=False, as_json=False):
            seen.update(check=check, as_json=as_json)
            return 0

        monkeypatch.setattr(data_libraries, "run_command", run_command)
        monkeypatch.setattr(
            init_command, "run_init", lambda **kw: pytest.fail("full init ran")
        )
        assert self._main(monkeypatch, "--data-libraries", "--check") == 0
        assert seen == {"check": True, "as_json": False}

    def test_the_flag_refuses_a_profile(self, monkeypatch, capsys):
        code = self._main(monkeypatch, "--data-libraries", "--profile", "rag")
        assert code == 2
        assert "--data-libraries" in capsys.readouterr().err

    def test_the_opt_out_reaches_setup(self, monkeypatch):
        seen = {}

        def run_init(**kwargs):
            seen.update(kwargs)
            return 0

        monkeypatch.setattr(init_command, "run_init", run_init)
        assert self._main(monkeypatch, "--yes", "--no-data-libraries") == 0
        assert seen["no_data_libraries"] is True
