# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Guards for gaia.agents.install_hints (#2240).

Every "agent X is not installed" message in the codebase used to recommend
`pip install gaia-agent-<id>` and `pip install "amd-gaia[agents]"` -- both
fail on a clean environment because the gaia-agent-* wheels aren't published
to PyPI yet. These tests pin the replacement contract: the generated message
must never recommend either broken command, and the source-install command it
does recommend must reference a directory that actually exists on disk.
"""

import importlib.metadata
import json
import sys
from pathlib import Path

import pytest

from gaia.agents import install_hints
from gaia.agents.install_hints import (
    _AGENT_SOURCE_SUBDIRS,
    agent_import_error_message,
    agent_not_installed_message,
    source_install_command,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "hub" / "agents"


CHAT_SPEC = (
    "gaia-agent-chat @ git+https://github.com/amd/gaia.git"
    f"@v{importlib.metadata.version('amd-gaia')}#subdirectory=hub/agents/chat/python"
)


@pytest.fixture
def stock_venv(monkeypatch):
    """`python -m venv`: pip importable, no uv anywhere (#2358)."""
    monkeypatch.setattr(install_hints, "_pip_available", lambda: True)
    monkeypatch.setattr(install_hints.shutil, "which", lambda _name: None)


@pytest.fixture
def installer_venv(monkeypatch):
    """The GAIA installer's `uv venv`: no pip, uv on PATH, never activated."""
    monkeypatch.setattr(install_hints, "_pip_available", lambda: False)
    monkeypatch.setattr(
        install_hints.shutil,
        "which",
        lambda name: "/home/u/.local/bin/uv" if name == "uv" else None,
    )


@pytest.fixture
def no_installer(monkeypatch, tmp_path):
    """No pip in the interpreter and no uv binary anywhere."""
    monkeypatch.setattr(install_hints, "_pip_available", lambda: False)
    monkeypatch.setattr(install_hints.shutil, "which", lambda _name: None)
    monkeypatch.setattr(install_hints.Path, "home", lambda: tmp_path)


class TestResolvePipFrontend:
    def test_pip_equipped_venv_uses_its_own_pip(self, stock_venv):
        frontend = install_hints.resolve_pip_frontend()
        assert frontend.argv == (sys.executable, "-m", "pip", "install")

    def test_pipless_uv_venv_targets_this_interpreter_explicitly(self, installer_venv):
        """A bare `uv pip install` finds no venv (none is activated) and
        `python -m pip` does not exist, so the only valid form names the
        interpreter with --python."""
        frontend = install_hints.resolve_pip_frontend()
        assert frontend.argv == (
            "/home/u/.local/bin/uv",
            "pip",
            "install",
            "--python",
            sys.executable,
        )
        assert frontend.display[0] == "uv"
        assert frontend.display[1:] == frontend.argv[1:]

    def test_uv_off_path_is_found_where_its_installer_puts_it(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(install_hints, "_pip_available", lambda: False)
        monkeypatch.setattr(install_hints.shutil, "which", lambda _name: None)
        monkeypatch.setattr(install_hints.Path, "home", lambda: tmp_path)
        exe = "uv.exe" if sys.platform == "win32" else "uv"
        uv = tmp_path / ".local" / "bin" / exe
        uv.parent.mkdir(parents=True)
        uv.write_text("")

        frontend = install_hints.resolve_pip_frontend()
        assert frontend.argv[0] == str(uv)
        assert frontend.display[0] == str(uv), "off PATH, so `uv` alone won't run"
        assert frontend.argv[1:] == ("pip", "install", "--python", sys.executable)

    def test_no_installer_raises_with_a_remedy(self, no_installer):
        with pytest.raises(install_hints.PackageInstallerUnavailableError) as exc:
            install_hints.resolve_pip_frontend()
        message = str(exc.value)
        assert sys.executable in message
        assert "docs.astral.sh/uv" in message
        assert "ensurepip" in message


class TestFormatCommand:
    def test_quotes_shell_meaningful_args_only(self):
        assert (
            install_hints.format_command(["uv", "pip", "install", "amd-gaia[ui]"])
            == 'uv pip install "amd-gaia[ui]"'
        )
        assert install_hints.format_command(["a b"]) == '"a b"'


class _FakeDist:
    def __init__(self, direct_url):
        self._direct_url = direct_url

    def read_text(self, name):
        assert name == "direct_url.json"
        return self._direct_url

    def locate_file(self, _path):
        return "/site-packages"


class TestGaiaExtrasInstallArgs:
    def _use(self, monkeypatch, direct_url):
        monkeypatch.setattr(
            install_hints.importlib.metadata,
            "distribution",
            lambda _name: _FakeDist(direct_url),
        )

    def test_wheel_install_asks_the_index(self, monkeypatch):
        self._use(monkeypatch, None)
        assert install_hints.gaia_extras_install_args(["rag"]) == ["amd-gaia[rag]"]

    def test_editable_checkout_reinstalls_itself(self, monkeypatch, tmp_path):
        self._use(
            monkeypatch,
            json.dumps({"url": tmp_path.as_uri(), "dir_info": {"editable": True}}),
        )
        assert install_hints.gaia_extras_install_args(["rag", "ui"]) == [
            "-e",
            f"{tmp_path}[rag,ui]",
        ]

    def test_non_editable_local_install_asks_the_index(self, monkeypatch, tmp_path):
        self._use(monkeypatch, json.dumps({"url": tmp_path.as_uri(), "dir_info": {}}))
        assert install_hints.gaia_extras_install_args(["ui"]) == ["amd-gaia[ui]"]

    def test_corrupt_install_record_is_loud(self, monkeypatch):
        self._use(monkeypatch, "{not json")
        with pytest.raises(RuntimeError, match="Reinstall GAIA"):
            install_hints.gaia_extras_install_args(["rag"])


class TestSourceInstallCommand:
    def test_stock_venv_gets_python_m_pip(self, stock_venv):
        """#2358: a stock `python -m venv` has no uv, so the hint must not
        require one."""
        cmd = source_install_command("gaia-agent-chat")
        assert cmd.startswith(f"{sys.executable} -m pip install ")
        assert f'"{CHAT_SPEC}"' in cmd

    def test_installer_venv_gets_uv_with_python_flag(self, installer_venv):
        """`python -m pip` fails with "No module named pip" on the installer's
        venv; the hint must be the uv form pinned to this interpreter."""
        cmd = source_install_command("gaia-agent-chat")
        assert cmd.startswith("uv pip install --python ")
        assert sys.executable in cmd
        assert cmd.endswith(f'"{CHAT_SPEC}"')
        assert "-m pip" not in cmd

    def test_no_installer_says_to_install_uv_first(self, no_installer):
        cmd = source_install_command("gaia-agent-chat")
        assert cmd.startswith("install uv (")
        assert "then run: uv pip install --python" in cmd
        assert f'"{CHAT_SPEC}"' in cmd

    def test_unknown_wheel_raises(self):
        with pytest.raises(KeyError):
            source_install_command("gaia-agent-does-not-exist")

    @pytest.mark.parametrize("wheel", sorted(_AGENT_SOURCE_SUBDIRS))
    def test_every_registered_subdir_exists_on_disk(self, wheel):
        """Guards against the map drifting from hub/agents/<id>/python (#2240)."""
        subdir = _AGENT_SOURCE_SUBDIRS[wheel]
        assert (AGENTS_DIR / subdir / "python").is_dir(), (
            f"{wheel} maps to hub/agents/{subdir}/python, which doesn't "
            "exist -- update _AGENT_SOURCE_SUBDIRS in install_hints.py."
        )


class TestAgentNotInstalledMessage:
    def test_never_recommends_the_broken_pip_commands(self):
        """Regression guard: neither broken install path appears (#2240)."""
        message = agent_not_installed_message(
            "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "pip install gaia-agent-chat`" not in message
        assert "amd-gaia[agents]" not in message

    def test_includes_working_source_install_command(self):
        message = agent_not_installed_message(
            "The chat agent is not installed", "gaia-agent-chat"
        )
        assert source_install_command("gaia-agent-chat") in message

    def test_references_tracking_issue(self):
        message = agent_not_installed_message(
            "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "github.com/amd/gaia/issues/2240" in message

    def test_next_step_is_appended(self):
        message = agent_not_installed_message(
            "The chat agent is not installed",
            "gaia-agent-chat",
            next_step="Then re-run `gaia chat`.",
        )
        assert message.endswith("Then re-run `gaia chat`.")

    def test_no_next_step_has_no_trailing_space(self):
        message = agent_not_installed_message(
            "The chat agent is not installed", "gaia-agent-chat"
        )
        assert not message.endswith(" ")


class TestAgentImportErrorMessage:
    def test_missing_top_level_module_name_reports_not_installed(self):
        # No CI environment can produce this for real -- gaia-agent-chat is
        # installed for the wheel's own tests. CPython assigns ImportError.name
        # exactly this way at raise time, so the constructed object is the
        # identical shape the real import machinery produces.
        error = ModuleNotFoundError("No module named 'gaia_agent_chat'")
        error.name = "gaia_agent_chat"
        message = agent_import_error_message(
            error, "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "is not installed" in message

    def test_version_skew_reports_failed_import_not_missing(self):
        """An ImportError raised *inside* the installed wheel must not be
        declared "not installed" (the old misdiagnosis) -- report the real
        error and both package versions. Produced via a real failed import of
        a symbol the core doesn't export, the exact shape of the 0.24.1 +
        gaia-agent-chat@main failure."""
        try:
            from gaia.config import _symbol_not_exported_by_core  # noqa: F401

            pytest.fail("import of an unexported symbol should fail")
        except ImportError as exc:
            error = exc
        message = agent_import_error_message(
            error, "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "is not installed" not in message
        assert "is installed, but it could not be imported" in message
        assert "_symbol_not_exported_by_core" in message
        assert "Detected versions:" in message
        assert "amd-gaia" in message
        assert (
            source_install_command("gaia-agent-chat", force_reinstall=True) in message
        )
        assert "--force-reinstall" in message
        assert "--no-deps" in message

    def test_missing_transitive_dependency_reports_failed_import(self):
        # Real ModuleNotFoundError from importing a module that will never
        # exist -- a wheel missing a transitive dep raises exactly this.
        try:
            import _gaia_missing_transitive_dep  # noqa: F401

            pytest.fail("import of a missing module should fail")
        except ModuleNotFoundError as exc:
            error = exc
        assert error.name == "_gaia_missing_transitive_dep"
        message = agent_import_error_message(
            error, "The chat agent is not installed", "gaia-agent-chat"
        )
        assert "is installed, but it could not be imported" in message
        assert "_gaia_missing_transitive_dep" in message


class TestGaiaExtraRequirements:
    """Adding extras installs their requirements, never GAIA itself."""

    DECLARED = [
        'pywin32; sys_platform == "win32"',
        "requests>=2",
        'faiss-cpu>=1.7.0; extra == "rag"',
        "pypdf; extra == 'rag'",
        'fastapi; extra == "ui"',
        'pypdf; extra == "ui"',
    ]

    def _declare(self, monkeypatch, declared):
        monkeypatch.setattr(
            install_hints.importlib.metadata, "requires", lambda _name: declared
        )

    def test_only_the_extras_requirements_are_returned(self, monkeypatch):
        self._declare(monkeypatch, self.DECLARED)
        assert install_hints.gaia_extra_requirements(["rag", "ui"]) == [
            "faiss-cpu>=1.7.0",
            "pypdf",
            "fastapi",
        ]

    def test_gaia_itself_is_never_reinstalled(self, monkeypatch):
        self._declare(monkeypatch, self.DECLARED)
        args = install_hints.gaia_extra_requirements(["rag"])
        assert not any("amd-gaia" in a or a == "-e" for a in args)

    def test_an_unknown_extra_is_loud(self, monkeypatch):
        self._declare(monkeypatch, self.DECLARED)
        with pytest.raises(RuntimeError, match="no extra"):
            install_hints.gaia_extra_requirements(["nope"])

    def test_a_condition_it_cannot_evaluate_is_loud(self, monkeypatch):
        self._declare(monkeypatch, ['x; extra == "rag" and sys_platform == "win32"'])
        with pytest.raises(RuntimeError, match="cannot evaluate"):
            install_hints.gaia_extra_requirements(["rag"])

    def test_gaia_not_installed_is_loud(self, monkeypatch):
        def missing(_name):
            raise install_hints.importlib.metadata.PackageNotFoundError("amd-gaia")

        monkeypatch.setattr(install_hints.importlib.metadata, "requires", missing)
        with pytest.raises(RuntimeError, match="not installed"):
            install_hints.gaia_extra_requirements(["rag"])

    def test_this_install_declares_every_init_profile_extra(self):
        from gaia.installer.init_command import INIT_PROFILES

        extras = {e for p in INIT_PROFILES.values() for e in p.get("pip_extras", [])}
        assert install_hints.gaia_extra_requirements(sorted(extras))
