# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``run_python`` computes without leaving a script in the user's project.

Without it the agent wrote a throwaway script into the repository for every
calculation, ran it with ``execute_python_file``, and could not delete it — or
skipped the script and reported numbers it never computed.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent.chat_agent import (  # noqa: E402
    ChatAgent,
    ChatAgentConfig,
    _imports_gaia_tools,
)
from gaia_agent.tool_bundles import FULL_CORE_TOOLS  # noqa: E402

from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION  # noqa: E402
from gaia.agents.base.checks import CHECK_RESULT_KEY, CheckResult  # noqa: E402
from gaia.agents.base.project_map import PROJECT_ROOT_ENV  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402


@pytest.fixture(autouse=True)
def no_inherited_project_root(monkeypatch):
    """The developer's own ``GAIA_PROJECT_ROOT`` must not steer these tests."""
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)


@pytest.fixture
def project(tmp_path):
    """A project with a CSV at its root and a package it can import."""
    root = tmp_path / "project"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'toy'\n", encoding="utf-8")
    (root / "build_times.csv").write_text("week,minutes\n1,10\n1,20\n2,5\n")
    (root / "src").mkdir()
    return root.resolve()


@pytest.fixture
def make_run_python():
    """Build a ChatAgent and hand back its registered ``run_python``."""
    saved = dict(_TOOL_REGISTRY)

    def build(allowed_path):
        with (
            patch("gaia_agent.chat_agent.RAGSDK"),
            patch("gaia_agent.chat_agent.RAGConfig"),
        ):
            agent = ChatAgent(
                ChatAgentConfig(silent_mode=True, allowed_paths=[str(allowed_path)])
            )
        entry = _TOOL_REGISTRY.get("run_python")
        assert entry is not None, "run_python was not registered"
        return agent, entry["function"]

    try:
        yield build
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_it_runs_a_snippet_and_returns_stdout(make_run_python, project, monkeypatch):
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)

    result = run_python(code="print(6 * 7)")

    assert result["status"] == "success", result
    assert result["return_code"] == 0, result["stderr"]
    assert result["stdout"].strip() == "42"
    assert result["has_errors"] is False


def test_relative_paths_resolve_against_the_project_root(
    make_run_python, project, monkeypatch
):
    """Launched from a subdirectory, the snippet still reads the root's files."""
    monkeypatch.chdir(project / "src")
    _agent, run_python = make_run_python(project)

    result = run_python(
        code=(
            "import csv, os\n"
            "from pkg import VALUE\n"
            "rows = list(csv.DictReader(open('build_times.csv')))\n"
            "print('total', sum(int(r['minutes']) for r in rows))\n"
            "print('cwd', os.getcwd())\n"
            "print('value', VALUE)\n"
        )
    )

    assert result["status"] == "success", result
    assert result["return_code"] == 0, result["stderr"]
    assert "total 35" in result["stdout"]
    assert f"cwd {project}" in result["stdout"]
    assert "value 42" in result["stdout"]


def test_the_snippet_never_lands_in_the_workspace(
    make_run_python, project, tmp_path, monkeypatch
):
    temp_dir = tmp_path / "system-temp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)
    before = _tree(project)

    result = run_python(code="print(__file__)")

    assert result["status"] == "success", result
    assert _tree(project) == before
    # It ran from the temp dir, and is not left behind there either. The
    # session's scratch dir may live there too; the snippet may not.
    assert result["stdout"].strip().startswith(str(temp_dir))
    assert not list(temp_dir.rglob("gaia-run-*"))


def test_the_snippet_is_removed_even_when_it_times_out(
    make_run_python, project, tmp_path, monkeypatch
):
    temp_dir = tmp_path / "system-temp"
    temp_dir.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)

    result = run_python(code="import time\ntime.sleep(30)", timeout=1)

    assert result["status"] == "error"
    assert not list(temp_dir.rglob("gaia-run-*"))


def test_files_the_snippet_writes_do_land_in_the_workspace(
    make_run_python, project, monkeypatch
):
    """Only the snippet stays out: its output is the point of running it."""
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)

    result = run_python(code="open('weekly.csv', 'w').write('week,total\\n')")

    assert result["status"] == "success", result
    assert (project / "weekly.csv").read_text() == "week,total\n"


def test_it_is_confirmation_gated():
    assert "run_python" in TOOLS_REQUIRING_CONFIRMATION


def test_it_is_always_on_for_the_flagship():
    assert "run_python" in FULL_CORE_TOOLS


def test_the_timeout_is_enforced(make_run_python, project, monkeypatch):
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)

    start = time.monotonic()
    result = run_python(code="import time\ntime.sleep(30)", timeout=1)

    assert result["status"] == "error"
    assert "Timed out after 1s" in result["error"]
    assert time.monotonic() - start < 20


def test_a_disallowed_project_the_user_chose_is_refused(
    make_run_python, project, tmp_path, monkeypatch
):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(project))
    _agent, run_python = make_run_python(elsewhere)

    result = run_python(code="open('ran.txt', 'w').write('x')")

    assert result["status"] == "error"
    assert "not in allowed paths" in result["error"]
    assert not (project / "ran.txt").exists()


def test_a_chosen_root_the_map_drops_is_named_in_the_refusal(
    make_run_python, project, tmp_path, monkeypatch
):
    allowed = tmp_path / "docs"
    allowed.mkdir()
    monkeypatch.chdir(allowed)
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(project))
    agent, run_python = make_run_python(allowed)
    monkeypatch.setattr(agent, "_project_map_root", lambda: None, raising=False)

    result = run_python(code="open('ran.txt', 'w').write('x')")

    assert result["status"] == "error"
    assert str(project) in result["error"]
    assert not (allowed / "ran.txt").exists()


def test_a_disallowed_repo_around_the_cwd_runs_in_the_scratch_dir(
    make_run_python, project, tmp_path, monkeypatch
):
    """The daemon's launch folder is not a project the user approved (#5765)."""
    allowed = tmp_path / "docs"
    allowed.mkdir()
    monkeypatch.chdir(project)
    agent, run_python = make_run_python(allowed)

    result = run_python(
        code="import os, sys; open('ran.txt', 'w').write('x'); "
        "print(os.getcwd()); print(os.environ.get('PYTHONPATH', ''))"
    )

    assert result["status"] == "success", result
    cwd, pythonpath = (result["stdout"].splitlines() + [""])[:2]
    assert Path(cwd).resolve() == agent.scratch_dir.resolve()
    assert str(project) not in pythonpath
    assert not (project / "ran.txt").exists()


def test_without_a_project_it_runs_from_the_current_directory(
    make_run_python, tmp_path, monkeypatch
):
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "data.txt").write_text("payload\n")
    monkeypatch.chdir(loose)
    _agent, run_python = make_run_python(loose)
    monkeypatch.setattr(
        "gaia.agents.base.project_map.resolve_project_root", lambda explicit: None
    )

    result = run_python(code="print(open('data.txt').read().strip())")

    assert result["status"] == "success", result
    assert result["stdout"].strip() == "payload"
    assert "note" not in result


def test_without_a_project_a_disallowed_cwd_runs_in_the_scratch_dir(
    make_run_python, tmp_path, monkeypatch
):
    server_cwd = tmp_path / "server"
    server_cwd.mkdir()
    allowed = tmp_path / "docs"
    allowed.mkdir()
    monkeypatch.chdir(server_cwd)
    agent, run_python = make_run_python(allowed)
    monkeypatch.setattr(
        "gaia.agents.base.project_map.resolve_project_root", lambda explicit: None
    )
    assert agent.scratch_dir is not None

    result = run_python(code="import os; print(os.getcwd())")

    assert result["status"] == "success", result
    assert Path(result["stdout"].strip()).resolve() == agent.scratch_dir.resolve()
    assert str(agent.scratch_dir) in result["note"]


def test_an_unusable_project_root_is_a_tool_error(
    make_run_python, project, monkeypatch
):
    monkeypatch.chdir(project)
    monkeypatch.setenv(PROJECT_ROOT_ENV, str(project / "pyproject.toml"))
    _agent, run_python = make_run_python(project)

    result = run_python(code="print(1)")

    assert result["status"] == "error"
    assert "not a directory" in result["error"]


def test_importing_a_gaia_tool_is_refused_with_the_tool_call_path(
    make_run_python, project, monkeypatch
):
    """The snippet is a separate process, so importing a tool can only fail.

    Left to the subprocess this returns a bare ``ImportError``, which reads as
    a typo worth retrying — the model reissued the same call until the step
    budget was gone (#4084).
    """
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)

    result = run_python(
        code=(
            "from gaia import transcribe_media\n"
            "print(transcribe_media(file_path='x.mp4'))"
        )
    )

    assert result["status"] == "error"
    assert result["has_errors"] is True
    assert "from gaia import transcribe_media" in result["error"]
    assert "directly" in result["error"]
    assert "ImportError" not in result["error"]


_TOOLS = {"transcribe_media", "write_file", "read_file", "install_skill"}


@pytest.mark.parametrize(
    "code",
    [
        "from gaia import transcribe_media",
        "print(1)\nfrom gaia import write_file",
        "if True:\n    from gaia import write_file",
        "from gaia import cli, write_file",
        "from gaia import write_file as save",
        "import gaia\ngaia.write_file('a.txt', 'x')",
        "import gaia as g\ng.read_file('a.txt')",
        # ``import gaia.x`` binds ``gaia`` too.
        "import gaia.agents\ngaia.transcribe_media('a.mp4')",
    ],
)
def test_importing_a_tool_through_gaia_is_detected(code):
    assert _imports_gaia_tools(code, _TOOLS) is not None


@pytest.mark.parametrize(
    "code",
    [
        "print(1 + 1)",
        "import json\nprint(json.dumps({'a': 1}))",
        "from pathlib import Path\nprint(Path('.').resolve())",
        # A different package whose name merely starts with the same letters.
        "import gaiatools",
        "from gaiatools import write_file",
        # Only a mention, not an import.
        "print('run from gaia import write_file to see it fail')",
        "# from gaia import write_file\nprint(1)",
        # The project's own modules, in a GAIA checkout.
        "import gaia",
        "import gaia.agents",
        "from gaia.agents.base.tools import tool",
        "from gaia.agents.base.claims import action_claims, claims_success",
        "from gaia import cli",
        # A module function that shares a tool's name is real code, not the tool.
        "from gaia.skills.install import install_skill",
        "import gaia.skills.install as m\nm.install_skill",
        "import gaia.agents\ngaia.agents.write_file",
        # Relative imports are the project's own.
        "from .gaia import write_file",
        # Not Python: the interpreter reports it, with the line.
        "from gaia import write_file(",
        "    from gaia import write_file",
    ],
)
def test_ordinary_and_project_imports_are_not_flagged(code):
    assert _imports_gaia_tools(code, _TOOLS) is None


def test_with_no_tools_known_nothing_is_flagged():
    assert _imports_gaia_tools("from gaia import write_file", set()) is None


def test_a_flagged_snippet_never_reaches_a_subprocess(
    make_run_python, project, monkeypatch
):
    """Refusing before ``subprocess.run`` is what makes the guard free."""
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)

    with patch("subprocess.run") as spawn:
        result = run_python(code="from gaia import transcribe_media")

    assert result["status"] == "error"
    spawn.assert_not_called()


@pytest.fixture
def src_checkout(tmp_path):
    """A ``src/``-layout checkout of a package named ``gaia``, like this repo."""
    root = tmp_path / "checkout"
    package = root / "src" / "gaia"
    (package / "agents").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "agents" / "__init__.py").write_text("", encoding="utf-8")
    (package / "marker.py").write_text("WHERE = 'this checkout'\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'gaia'\n")
    return root.resolve()


def test_a_src_layout_checkout_imports_its_own_code(
    make_run_python, src_checkout, monkeypatch
):
    """Inside the project's own repo, its modules import from the checkout.

    Refusing every ``gaia`` import here, or resolving it to the installed copy,
    left the model hand-copying code and reporting the copy's output as proof.
    """
    monkeypatch.chdir(src_checkout)
    _agent, run_python = make_run_python(src_checkout)

    result = run_python(
        code="from gaia.marker import WHERE\nimport gaia\nprint(WHERE, gaia.__file__)"
    )

    assert result["status"] == "success", result
    assert "this checkout" in result["stdout"]
    assert str(src_checkout / "src") in result["stdout"]


def test_an_empty_src_folder_is_not_put_on_the_path(make_run_python, project):
    """``src/`` with no package (a JS app's, say) adds nothing to the path."""
    from gaia_agent.chat_agent import _project_import_paths

    assert _project_import_paths(project) == [str(project)]


def test_a_tool_imported_from_a_submodule_gets_the_tool_hint(
    make_run_python, src_checkout, monkeypatch
):
    """Not refused up front (a module may define that name); explained if it fails."""
    monkeypatch.chdir(src_checkout)
    _agent, run_python = make_run_python(src_checkout)

    result = run_python(code="from gaia.agents import run_python")

    assert result["status"] == "error"
    assert "call it directly as a tool" in result.get("note", "")


def test_an_unrelated_import_error_gets_no_tool_hint(
    make_run_python, src_checkout, monkeypatch
):
    monkeypatch.chdir(src_checkout)
    _agent, run_python = make_run_python(src_checkout)

    result = run_python(code="from gaia.agents import not_a_tool_name")

    assert result["status"] == "error"
    assert "directly as a tool" not in result.get("note", "")


def test_the_docstring_warns_against_importing_tools(make_run_python, project):
    """The model reasons from the description, so the rule has to live there."""
    make_run_python(project)

    doc = (_TOOL_REGISTRY["run_python"]["description"] or "").lower()

    assert "from gaia import" in doc
    assert "directly as a tool" in doc


# ---------------------------------------------------------------------------
# A snippet that runs the tests reports the outcome as a fact
# ---------------------------------------------------------------------------

_RUN_PYTEST = (
    "import sys, pytest\n"
    "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', 'test_toy.py']))\n"
)


@pytest.mark.parametrize(
    "test_body,passed,summary_word",
    [
        ("def test_ok():\n    assert 1 + 1 == 2\n", True, "1 passed"),
        ("def test_bad():\n    assert 1 + 1 == 3\n", False, "1 failed"),
        (
            "def test_many(subtests):\n"
            "    for i in range(3):\n"
            "        with subtests.test(i=i):\n"
            "            assert i >= 0\n",
            True,
            "3 subtests passed",
        ),
    ],
    ids=["pass", "fail", "subtests"],
)
def test_a_pytest_run_attaches_its_check(
    make_run_python, project, monkeypatch, test_body, passed, summary_word
):
    if "subtests" in test_body and not hasattr(pytest, "Subtests"):
        pytest.importorskip("pytest_subtests")
    (project / "test_toy.py").write_text(test_body, encoding="utf-8")
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)

    result = run_python(code=_RUN_PYTEST)

    check = CheckResult.from_result(result)
    assert check is not None, result
    assert check.label == "pytest"
    assert check.kind == "test"
    assert check.passed is passed
    assert summary_word in check.summary


def test_a_snippet_that_is_not_a_test_run_declares_no_check(
    make_run_python, project, monkeypatch
):
    monkeypatch.chdir(project)
    _agent, run_python = make_run_python(project)

    result = run_python(code="print('5 passed')")

    assert result[CHECK_RESULT_KEY] is None


def test_an_unreadable_src_folder_fails_loudly(project, monkeypatch):
    """Guessing would run the installed copy while reporting on the project."""
    from gaia_agent.chat_agent import _project_import_paths

    real_iterdir = Path.iterdir

    def _denied(self):
        if self.name == "src":
            raise PermissionError("access denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _denied)

    with pytest.raises(OSError, match="src"):
        _project_import_paths(project)
