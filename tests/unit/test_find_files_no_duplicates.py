# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A file found under two search roots is listed once.

The default ``smart`` scope searches the working folder and then the home
folder, so a working folder under home was walked twice and every match in it
was listed twice, using up the result limit on repeats.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin


class _Sandbox:
    def __init__(self, *paths):
        self.allowed_paths = {Path(p).resolve() for p in paths}

    def is_path_allowed(self, path, prompt_user=True):
        resolved = Path(path).resolve()
        return any(
            resolved == root or root in resolved.parents for root in self.allowed_paths
        )

    def validate_read(self, path, prompt_user=True):
        if not self.is_path_allowed(path, prompt_user=prompt_user):
            return False, f"Access denied: '{path}' is not in allowed paths"
        return True, ""

    def confine_roots(self, roots):
        confined = []
        for root in map(Path, roots):
            if self.is_path_allowed(root):
                confined.append(root)
            else:
                confined.extend(
                    a for a in sorted(self.allowed_paths) if root in a.parents
                )
        return confined


def _find_files(*allowed):
    class Agent(FileSystemToolsMixin):
        def __init__(self):
            self._web_client = None
            self._path_validator = _Sandbox(*allowed)
            self._fs_index = None
            self._tools = {}
            self._bookmarks = {}

    registered = {}

    def mock_tool(atomic=True):
        def decorator(func):
            registered[func.__name__] = func
            return func

        return decorator

    with patch("gaia.agents.base.tools.tool", mock_tool):
        Agent().register_filesystem_tools()
    return registered["find_files"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home whose working folder is a project inside it."""
    home = (tmp_path / "home").resolve()
    project = home / "code" / "proj"
    project.mkdir(parents=True)
    (project / "notes_a.md").write_text("alpha needle\n")
    (project / "notes_b.md").write_text("beta needle\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(project)
    return home


def _listed(out):
    return re.findall(r"^\s+\d+\. (\S+)", out, flags=re.M)


@pytest.mark.parametrize("search_type", ["name", "content"])
def test_a_working_folder_under_home_lists_each_file_once(home, search_type):
    query = "notes_*" if search_type == "name" else "needle"
    out = _find_files(home)(query, scope="smart", search_type=search_type)
    listed = _listed(out)
    assert sorted(listed) == sorted(set(listed)), out
    assert len(listed) == 2, out
    assert out.startswith("Found 2 result(s)"), out
