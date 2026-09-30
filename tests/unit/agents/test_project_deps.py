# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The project map says which declared Python dependencies are missing."""

import sys

import pytest

from gaia.agents.base.project_deps import declared_python_deps, missing_deps
from gaia.agents.base.project_map import ProjectMap, render_project_map


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


def test_nothing_declared_means_nothing_to_ask():
    assert missing_deps([], sys.executable) is None


def render(**fields):
    pm = ProjectMap(root="/r", is_repository=True, vcs=".git", **fields)
    return render_project_map(pm, work_roots=["/r"])


def test_missing_deps_and_the_work_roots_are_on_the_map():
    text = render(python_deps=["asgiref", "sqlparse"], missing_python_deps=["asgiref"])
    assert "NOT installed for `python`: asgiref (of 2 declared)" in text
    assert "You can read and write only under: /r" in text


def test_a_fully_installed_project_says_so():
    assert "all 2 declared are installed" in render(
        python_deps=["a", "b"], missing_python_deps=[]
    )


def test_an_unchecked_project_says_nothing_about_deps():
    assert "dependencies" not in render(python_deps=["a"], missing_python_deps=None)
