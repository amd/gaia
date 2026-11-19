# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The project map says which declared Python dependencies are missing."""

import sys

import pytest

from gaia.agents.base import project_map
from gaia.agents.base.project_deps import declared_python_deps, missing_deps
from gaia.agents.base.project_map import (
    ProjectMap,
    build_project_map,
    clear_project_map_cache,
    python_deps_line,
    render_project_map,
)


@pytest.mark.parametrize(
    "name, text",
    [
        (
            "pyproject.toml",
            '[project]\nname = "x"\ndependencies = ["asgiref>=3.2", "sqlparse"]\n',
        ),
        (
            "setup.cfg",
            "[options]\ninstall_requires =\n    asgiref >= 3.2\n    sqlparse\n"
            "    tzdata; sys_platform == 'nosuchplatform'\n",
        ),
        (
            "setup.py",
            "setup(name='x', install_requires=['asgiref>=3.2', \"sqlparse\"])\n",
        ),
        ("requirements.txt", "# pinned\nasgiref==3.2\nsqlparse\n-e .\n"),
    ],
)
def test_each_manifest_kind_yields_its_runtime_deps(tmp_path, name, text):
    (tmp_path / name).write_text(text)
    assert declared_python_deps(tmp_path) == ["asgiref", "sqlparse"]


def test_a_folder_with_no_manifest_declares_nothing(tmp_path):
    assert declared_python_deps(tmp_path) == []


def test_the_interpreter_is_asked_which_are_missing():
    missing = missing_deps(["pytest", "surely-not-installed-xyz"], sys.executable)
    assert missing == ["surely-not-installed-xyz"]


def test_an_install_is_seen_without_restarting(tmp_path, monkeypatch):
    """PYTHONPATH counts, and a new package in a search path clears the answer."""
    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(site))
    name = "surely-not-installed-xyz"
    assert missing_deps([name], sys.executable) == [name]
    dist = site / "surely_not_installed_xyz-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
    )
    assert missing_deps([name], sys.executable) == []


def test_nothing_declared_means_nothing_to_ask():
    assert missing_deps([], sys.executable) is None


def test_the_map_rechecks_deps_when_its_fingerprint_has_not_changed(
    tmp_path, monkeypatch
):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["asgiref"]\n'
    )
    answers = [["asgiref"], []]
    monkeypatch.setattr(project_map, "missing_deps", lambda names: answers.pop(0))
    clear_project_map_cache()
    try:
        assert build_project_map(tmp_path).missing_python_deps == ["asgiref"]
        assert build_project_map(tmp_path).missing_python_deps == []
    finally:
        clear_project_map_cache()


def render(work_roots=("/r",), **fields):
    pm = ProjectMap(root="/r", is_repository=True, vcs=".git", **fields)
    return render_project_map(pm, work_roots=work_roots)


def deps_line(**fields):
    return python_deps_line(
        ProjectMap(root="/r", is_repository=True, vcs=None, **fields)
    )


def test_the_work_roots_are_on_the_map():
    assert "You can read and write without asking only under: /r" in render()


def test_deps_ride_in_the_turn_not_on_the_map():
    """An install changes them mid-session; the system prompt must not change."""
    fields = {"python_deps": ["asgiref", "sqlparse"], "missing_python_deps": ["a"]}
    assert "dependencies" not in render(**fields)
    assert "NOT installed for `python`: a (of 2 declared)" in deps_line(**fields)


def test_a_long_list_of_work_roots_is_capped():
    text = render(work_roots=[f"/r{n}" for n in range(7)])
    assert "/r3; and 3 more" in text and "/r4" not in text


def test_a_fully_installed_project_says_so():
    assert deps_line(python_deps=["a", "b"], missing_python_deps=[]) == (
        "[Python dependencies: all 2 declared are installed.]"
    )


def test_an_unchecked_project_says_nothing_about_deps():
    assert deps_line(python_deps=["a"], missing_python_deps=None) == ""


def test_setup_py_apostrophe_in_a_comment_invents_no_dependency(tmp_path):
    """A quote in a comment paired with a marker's quote and named `win32`."""
    (tmp_path / "setup.py").write_text(
        "setup(install_requires=[\n"
        "    # don't pin this\n"
        "    'pywin32; sys_platform == \"win32\"',\n"
        "    'asgiref',\n"
        "])\n"
    )
    deps = declared_python_deps(tmp_path)
    assert "win32" not in deps
    assert deps == (["pywin32", "asgiref"] if sys.platform == "win32" else ["asgiref"])


def test_setup_py_markers_and_extras_never_become_names(tmp_path):
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\n"
        "setup(\n"
        "    name='x',\n"
        "    install_requires=[\n"
        '        "asgiref>=3.2",\n'
        "        # keyring's backend isn't win32security\n"
        "        \"sqlparse[extra]>=1; python_version >= '3.0'\",\n"
        "        'tzdata; sys_platform == \"nosuchplatform\"',\n"
        "        '''bracket]in[a string''',\n"
        "    ],\n"
        ")\n"
    )
    assert declared_python_deps(tmp_path) == ["asgiref", "sqlparse"]


def test_setup_py_follows_a_named_list_and_skips_what_it_cannot_evaluate(tmp_path):
    (tmp_path / "setup.py").write_text(
        "import os\n"
        "BASE = ['asgiref']\n"
        "setup(install_requires=BASE + [f'x{os.sep}', get_dep(), 'sqlparse'])\n"
    )
    assert declared_python_deps(tmp_path) == ["asgiref", "sqlparse"]


def test_setup_py_self_referencing_name_does_not_loop(tmp_path):
    (tmp_path / "setup.py").write_text(
        "REQS = REQS + ['asgiref']\nsetup(install_requires=REQS)\n"
    )
    assert declared_python_deps(tmp_path) == ["asgiref"]


def test_setup_py_is_read_as_data_never_executed(tmp_path):
    marker = tmp_path / "ran"
    (tmp_path / "setup.py").write_text(
        f"open({str(marker)!r}, 'w').close()\nsetup(install_requires=['asgiref'])\n"
    )
    assert declared_python_deps(tmp_path) == ["asgiref"]
    assert not marker.exists()


@pytest.mark.parametrize(
    "text",
    [
        "setup(install_requires=[\n",  # syntax error
        "setup(name='x')\n",  # no install_requires
        "setup(install_requires=load_reqs())\n",  # not a literal
        "x = 1\0\n",  # NUL byte
    ],
)
def test_setup_py_that_cannot_be_read_as_data_declares_nothing(tmp_path, text):
    (tmp_path / "setup.py").write_text(text)
    assert declared_python_deps(tmp_path) == []
