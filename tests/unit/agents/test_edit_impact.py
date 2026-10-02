# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An edit to a Python function reports the project code it reaches."""

import subprocess

import pytest

from gaia.agents.tools.edit_impact import edit_impact

LIB = """def levels(z, n):
    return n


class Manager:
    def create(self, **kw):
        return kw
"""


@pytest.fixture
def repo(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True).returncode:
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "lib.py").write_text(LIB)
    (tmp_path / "pkg" / "tri.py").write_text(
        "from pkg.lib import levels\n\nx = levels(1, 2)\n"
    )
    (tmp_path / "contrib").mkdir()
    (tmp_path / "contrib" / "generic.py").write_text(
        "class GenericManager:\n    def create(self, **kw):\n        return kw\n"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def test_a_changed_signature_lists_its_callers_in_other_folders(repo):
    after = LIB.replace("def levels(z, n):", "def levels(z, n, dtype):")
    impact = edit_impact(repo / "pkg" / "lib.py", LIB, after)
    [changed] = impact["signature_changed"]
    assert changed["function"] == "levels"
    assert any(site.startswith("pkg/tri.py:3:") for site in changed["sites"])
    assert not any("def levels" in site for site in changed["sites"])


def test_an_edited_method_lists_the_other_classes_defining_it(repo):
    after = LIB.replace("return kw", "return dict(kw)")
    impact = edit_impact(repo / "pkg" / "lib.py", LIB, after)
    [method] = impact["other_definitions"]
    assert method["method"] == "Manager.create"
    assert method["also_defined_in"][0].startswith("contrib/generic.py:2:")
    assert "signature_changed" not in impact


def test_a_body_only_edit_to_a_plain_function_reports_nothing(repo):
    after = LIB.replace("return n", "return n + 1")
    assert edit_impact(repo / "pkg" / "lib.py", LIB, after) is None


@pytest.mark.parametrize(
    "name, before, after",
    [
        ("notes.md", "# a", "# b"),
        ("lib.py", LIB, "def broken(:\n"),
    ],
)
def test_non_python_or_unparsable_edits_report_nothing(repo, name, before, after):
    assert edit_impact(repo / "pkg" / name, before, after) is None


def test_a_file_outside_a_git_checkout_reports_nothing(tmp_path):
    after = LIB.replace("def levels(z, n):", "def levels(z, n, dtype):")
    assert edit_impact(tmp_path / "lib.py", LIB, after) is None
