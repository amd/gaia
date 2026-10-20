# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which ``run_python`` / ``execute_python_file`` calls make a turn's undo
incomplete: only those that could have written files, each described well
enough to recognise under the answer.
"""

from __future__ import annotations

import textwrap
import time
from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent_chat")

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402
from gaia_agent_chat.python_jobs import (  # noqa: E402
    describe_code,
    may_write_files,
)

from gaia.agents.base import file_snapshots as fs  # noqa: E402
from gaia.agents.base.project_map import PROJECT_ROOT_ENV  # noqa: E402
from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402

SID = "session-1"


# ── the static check ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "code",
    [
        "print(open('log.txt').read())",
        "with open('log.txt', 'r', encoding='utf-8') as f:\n    print(len(f.readlines()))",
        "from pathlib import Path\nprint(Path('a.txt').open('rb').read())",
        "import pandas as pd\ndf = pd.read_csv('x.csv')\nprint(df.to_csv(index=False))",
        "import pandas as pd\ndf = pd.read_csv('x.csv').rename(columns={'a': 'b'})\nprint(df.copy())",
        "import json\nprint(json.dumps({'a': 1}))",
        "items = [1, 2]\nitems.remove(1)\nprint('x'.replace('x', 'y'))",
        "import shutil\nprint(shutil.which('git'))",
        "import os\nprint(os.listdir('.'), os.path.exists('a'))",
        "import zipfile\nprint(zipfile.ZipFile('a.zip').namelist())",
        "import openpyxl\nwb = openpyxl.load_workbook('a.xlsx')\nprint(wb.sheetnames)",
    ],
)
def test_code_that_only_reads_is_read_only(code):
    assert may_write_files(code) is False


@pytest.mark.parametrize(
    "code",
    [
        "open('out.txt', 'w').write('x')",
        "open('out.txt', mode='a')",
        "mode = 'w'\nopen('out.txt', mode)",
        "from pathlib import Path\nPath('a.txt').write_text('x')",
        "from pathlib import Path\nPath('a.txt').open('w')",
        "from pathlib import Path\nPath('a.txt').rename('b.txt')",
        "from pathlib import Path\nPath('a.txt').unlink()",
        "import pandas as pd\npd.DataFrame().to_csv('out.csv')",
        "import pandas as pd\npd.DataFrame().to_json(path_or_buf='out.json')",
        "import pandas as pd\npd.DataFrame().to_excel('out.xlsx')",
        "import matplotlib.pyplot as plt\nplt.savefig('chart.png')",
        "import os\nos.remove('a.txt')",
        "import os\nos.system('del a.txt')",
        "import shutil\nshutil.copy('a', 'b')",
        "import subprocess\nsubprocess.run(['git', 'clean', '-fd'])",
        "import zipfile\nzipfile.ZipFile('a.zip', 'w')",
        "import xlsxwriter\nxlsxwriter.Workbook('a.xlsx')",
        "import sqlite3\nsqlite3.connect('a.db')",
        "exec(open('other.py').read())",
        "def (",
    ],
)
def test_code_that_may_write_is_flagged(code):
    assert may_write_files(code) is True


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (
            '"""Count errors per hour in the incident logs."""\nprint(1)',
            "Count errors per hour in the incident logs.",
        ),
        (
            "# reads incident logs, counts errors by hour\nimport re\nprint(1)",
            "reads incident logs, counts errors by hour",
        ),
        (
            "#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n# summarise\nprint(1)",
            "summarise",
        ),
        (
            "import pandas as pd\nfrom pathlib import Path\n\ndf = pd.read_csv('a.csv')",
            "df = pd.read_csv('a.csv')",
        ),
        ("   \n", ""),
    ],
)
def test_describe_code(code, expected):
    assert describe_code(code) == expected


def test_a_long_description_is_cut():
    text = describe_code("x = '" + "a" * 300 + "'")
    assert len(text) == 120
    assert text.endswith("…")


# ── the turn record ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("project")
    (root / "pyproject.toml").write_text("[project]\nname = 'toy'\n")
    (root / "incidents.log").write_text("ERROR a\nINFO b\nERROR c\n")
    return root.resolve()


@pytest.fixture(autouse=True)
def no_inherited_project_root(monkeypatch, project):
    monkeypatch.delenv(PROJECT_ROOT_ENV, raising=False)
    monkeypatch.chdir(project)


@pytest.fixture(scope="module")
def tools(project):
    """One ChatAgent rooted at *project* (building one takes ~10 s)."""
    saved = dict(_TOOL_REGISTRY)
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv(PROJECT_ROOT_ENV, raising=False)
        mp.chdir(project)
        with (
            patch("gaia_agent_chat.agent.RAGSDK"),
            patch("gaia_agent_chat.agent.RAGConfig"),
        ):
            ChatAgent(ChatAgentConfig(silent_mode=True, allowed_paths=[str(project)]))
        try:
            yield dict(_TOOL_REGISTRY)
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)


def _run(tools, root, name, **kwargs):
    # The folder scan allows a second of slack for coarse modification times,
    # so files the previous test wrote must be older than that.
    time.sleep(1.1)
    with fs.recording_turn(SID, root=root):
        result = tools[name]["function"](**kwargs)
    assert result.get("return_code") == 0, result
    return fs.list_turns(SID, root=root)


def test_read_only_snippets_leave_no_warning(tools, tmp_path, project):
    code = textwrap.dedent(f"""
        # count errors in the incident log
        lines = open(r"{project / 'incidents.log'}").read().splitlines()
        print(sum(1 for line in lines if line.startswith("ERROR")))
        """)
    assert _run(tools, tmp_path, "run_python", code=code) == []


def test_a_writing_snippet_is_described_and_marks_the_turn(tools, tmp_path, project):
    code = textwrap.dedent(f"""
        # save the error count
        open(r"{project / 'count.txt'}", "w").write("2")
        """)
    (turn,) = _run(tools, tmp_path, "run_python", code=code)
    assert turn["incomplete"] is True
    assert turn["untracked"] == [
        {
            "kind": "run_python",
            "detail": "save the error count",
            "paths": [],
            "may_write": True,
        }
    ]


def test_a_write_the_check_misses_is_caught_by_the_folder_scan(
    tools, tmp_path, project
):
    target = project / "sneaky.txt"
    code = textwrap.dedent(f"""
        import pathlib
        getattr(pathlib.Path(r"{target}"), "write_" + "text")("x")
        """)
    assert may_write_files(code) is False
    (turn,) = _run(tools, tmp_path, "run_python", code=code)
    assert turn["incomplete"] is True
    assert str(target) in turn["untracked"][0]["paths"]


def test_a_read_only_script_leaves_no_warning(tools, tmp_path, project):
    script = project / "count_errors.py"
    script.write_text(
        "lines = open('incidents.log').read().splitlines()\nprint(len(lines))\n"
    )
    assert _run(tools, tmp_path, "execute_python_file", file_path=str(script)) == []
