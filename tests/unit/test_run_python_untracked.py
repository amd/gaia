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
        # Typical log and data analysis.
        "import re\nfrom collections import Counter\n"
        "hits = Counter(re.findall(r'ERROR (\\w+)', open('app.log').read()))\n"
        "print(hits.most_common(5))",
        "import json, sys\njson.dump({'a': 1}, sys.stdout)",
        "import sys\nsys.stdout.write('done\\n')",
        "import glob, os\nfor p in glob.glob('*.log'):\n    print(p, os.stat(p).st_size)",
        "from pathlib import Path\nfor p in Path('.').rglob('*.py'):\n"
        "    print(p, len(p.read_text().splitlines()))",
        "import csv\nwith open('a.csv', newline='') as f:\n"
        "    print(sum(1 for _ in csv.reader(f)))",
        "import gzip\nprint(gzip.open('a.log.gz', 'rt').read()[:100])",
        "import tarfile\nprint(tarfile.open('a.tgz', 'r:gz').getnames())",
        "import numpy as np\nprint(np.load('a.npy').mean(), np.copy([1]))",
        "import copy\nprint(copy.copy([1]))",
        "import pandas as pd\ns = pd.read_csv('a.csv')['x']\n"
        "print(s.str.extract(r'(\\d+)'), pd.to_datetime(s).max())",
        "import zipfile\nprint(zipfile.ZipFile('a.zip').open('m.txt').read())",
        "import datetime as dt\nprint(dt.datetime.now().replace(year=2020))",
        "print(getattr(object(), 'shape', None))",
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
    "code",
    [
        # open() in every writing mode, and modes that cannot be read off the call.
        "open('a', 'a')",
        "open('a', 'x')",
        "open('a', 'r+')",
        "open('a', 'rb+')",
        "open('a', 'wb')",
        "open(file='a', mode='w')",
        "m = 'w'\nopen('a', mode=m)",
        "args = ('a', 'w')\nopen(*args)",
        "kw = {'mode': 'w'}\nopen('a', **kw)",
        "o = open\no('a', 'w')",
        "f = list(map(open, ['a']))",
        "import io\nio.open('a', 'w')",
        "import io\nio.FileIO('a', 'w')",
        "import codecs\ncodecs.open('a', 'w', 'utf-8')",
        "from io import open as o\no('a', 'w')",
        "import gzip\ngzip.open('a.gz', 'wt')",
        "import bz2\nbz2.BZ2File('a.bz2', 'w')",
        # pathlib
        "from pathlib import Path\nPath('a').write_bytes(b'x')",
        "from pathlib import Path\nPath('a').touch()",
        "from pathlib import Path\nPath('a').mkdir(parents=True)",
        "from pathlib import Path\nPath('a').rmdir()",
        "from pathlib import Path\np = Path('a')\np.rename('b')",
        "from pathlib import Path\np = Path('a')\np.replace('b')",
        "from pathlib import Path\nPath('a').rename(target='b')",
        "from pathlib import Path\nPath.rename(Path('a'), 'b')",
        "from pathlib import Path\nPath.unlink(Path('a'))",
        "from pathlib import Path\np = Path('a')\np.open(mode)",
        "from pathlib import Path\nPath('a').open(mode='a')",
        "from pathlib import Path\nPath('a').symlink_to('b')",
        "from pathlib import Path\nPath('a').chmod(0o777)",
        "from pathlib import Path\nPath('a').copy('b')",
        "from pathlib import Path\nfor p in Path('.').glob('*.tmp'):\n    p.unlink()",
        "from pathlib import Path\nf = Path('a').write_text\nf('x')",
        "import pathlib\npathlib.os.remove('a')",
        # os, with and without aliases
        "import os as o\no.remove('a')",
        "from os import remove\nremove('a')",
        "from os import remove as r\nr('a')",
        "from os import *\nremove('a')",
        "import os\nos.rename('a', 'b')",
        "import os\nos.replace('a', 'b')",
        "import os\nos.makedirs('a')",
        "import os\nos.unlink('a')",
        "import os\nos.rmdir('a')",
        "import os\nos.popen('rm a')",
        "import os\nos.execv('/bin/rm', ['rm', 'a'])",
        "import os\nos.fdopen(os.open('a', os.O_WRONLY))",
        "import os\nos.truncate('a', 0)",
        "import os\nos.chdir('/')",
        "import os\nr = os.remove\nr('a')",
        "import os\nm = os\nm.remove('a')",
        "import os\nmods = [os]\nmods[0].remove('a')",
        "import os.path\nos.remove('a')",
        "import os\nfor f in os.listdir('.'):\n    os.remove(f)",
        # shutil and processes
        "import shutil\nshutil.rmtree('a')",
        "import shutil as sh\nsh.move('a', 'b')",
        "from shutil import copyfile\ncopyfile('a', 'b')",
        "import shutil\nshutil.make_archive('a', 'zip', '.')",
        "import subprocess as sp\nsp.run(['rm', 'a'])",
        "from subprocess import run\nrun(['rm', 'a'])",
        "from subprocess import Popen\nPopen(['rm', 'a'])",
        "import multiprocessing",
        "import asyncio\nasyncio.run(asyncio.create_subprocess_exec('rm', 'a'))",
        # archives
        "import tarfile\ntarfile.open('a.tgz', 'w:gz')",
        "import tarfile\ntarfile.open('a.tar', mode='a')",
        "import tarfile\ntarfile.open('a.tgz').extractall('out')",
        "import zipfile\nzipfile.ZipFile('a.zip').extract('m.txt')",
        "import zipfile\nzipfile.ZipFile('a.zip', mode='a')",
        "import zipfile\nz = zipfile.ZipFile('a.zip')\nz.open('m.txt', 'w')",
        "from zipfile import ZipFile\nZipFile('a.zip', 'x')",
        # data libraries
        "import pandas as pd\npd.DataFrame().to_csv(path_or_buf='a.csv')",
        "import pandas as pd\nkw = {'path_or_buf': 'a.csv'}\npd.DataFrame().to_csv(**kw)",
        "import pandas as pd\npd.DataFrame().to_parquet('a.parquet')",
        "import pandas as pd\npd.DataFrame().to_pickle('a.pkl')",
        "import pandas as pd\npd.DataFrame().to_html('a.html')",
        "import pandas as pd\npd.DataFrame().to_some_future_format('a')",
        "import pandas as pd\nwith pd.ExcelWriter('a.xlsx') as w:\n    pass",
        "import pandas as pd\npd.HDFStore('a.h5')",
        "import pandas as pd\npd.read_pickle('a.pkl')",
        "import pandas as pd\npd.eval('1 + 1')",
        "import pandas as pd\nf = pd.DataFrame.to_csv\nf(pd.DataFrame(), 'a.csv')",
        "from pandas import ExcelWriter",
        "import numpy as np\nnp.save('a.npy', [1])",
        "import numpy as np\nnp.savetxt('a.txt', [1])",
        "import numpy as np\nnp.array([1]).tofile('a.bin')",
        "import numpy as np\nnp.memmap('a.bin', mode='r')",
        "import numpy as np\nnp.load('a.npy', allow_pickle=True)",
        "import numpy as np\nnp.load('a.npy', mmap_mode='r+')",
        "from numpy import load\nload('a.npy', allow_pickle=True)",
        "import openpyxl\nwb = openpyxl.load_workbook('a.xlsx')\nwb.save('a.xlsx')",
        "import openpyxl\nwb = openpyxl.Workbook()\nwb.save('b.xlsx')",
        "from PIL import Image\nImage.open('a.png').save('b.png')",
        "import matplotlib.pyplot as plt\nplt.gcf().savefig('a.png')",
        "import docx\ndocx.Document().save('a.docx')",
        "from pypdf import PdfWriter\nPdfWriter().write('a.pdf')",
        # json and other writers that need a file opened to write
        "import json\njson.dump({}, open('a.json', 'w'))",
        "import json\nfrom pathlib import Path\njson.dump({}, Path('a.json').open('w'))",
        "import json\nwith open('a.json', 'w') as f:\n    json.dump({}, f)",
        "import csv\ncsv.writer(open('a.csv', 'w')).writerow([1])",
        "print('x', file=open('a.txt', 'a'))",
        "import matplotlib.pyplot as plt\nplt.gcf().canvas.print_png('a.png')",
        "import tarfile\ntarfile.TarFile.taropen('a.tar', 'w')",
        "import zipfile\nzipfile.PyZipFile('a.zip', 'w')",
        # databases
        "import pandas as pd\npd.read_sql('DROP TABLE t', 'sqlite:///a.db')",
        "import sqlite3\nc = sqlite3.connect('a.db')\nc.execute('DELETE FROM t')\nc.commit()",
        "from sqlite3 import connect\nconnect('a.db')",
        "import dbm",
        "import shelve\nshelve.open('a')",
        # dynamic code and reflection
        "eval(\"__import__('os').remove('a')\")",
        "compile('x', 'f', 'exec')",
        "__import__('os').remove('a')",
        "import importlib\nimportlib.import_module('os').remove('a')",
        "import os\ngetattr(os, 'remove')('a')",
        "from pathlib import Path\ngetattr(Path('a'), 'unlink')()",
        "from pathlib import Path\ngetattr(Path('a'), 'write_' + 'text')('x')",
        "globals()['__builtins__'].open('a', 'w')",
        "vars()['open']('a', 'w')",
        "__builtins__.open('a', 'w')",
        "import builtins\nbuiltins.open('a', 'w')",
        "().__class__.__base__.__subclasses__()",
        "import sys\nsys = open('a', 'w')\nsys.stdout.write('x')",
        "from io import open as o\nopeners = [o]",
        "import sys\nsys.modules['os'].remove('a')",
        "import json\njson.codecs.sys.modules['os'].remove('a')",
        "import pickle\npickle.loads(b'')",
        "import runpy\nrunpy.run_path('x.py')",
        "import ctypes",
        "import tempfile\ntempfile.mkstemp()",
        "import logging\nlogging.basicConfig(filename='a.log')",
        # a module that is not known to be read-only
        "import requests\nrequests.get('http://x')",
        "import mylib\nmylib.process('a.csv')",
        "from mylib import process\nprocess('a.csv')",
        "from . import helper",
        "import xlsxwriter",
        "import fitz\nfitz.open('a.pdf').save('b.pdf')",
        # a module name shadowed to dodge an exemption
        "import numpy as p\np = __import__('pathlib').Path('a')\np.rename('b')",
        "import json\njson = open('a', 'w')\njson.dump({}, json)",
        "import io\nfrom pathlib import Path\nio = Path('a')\nio.open('w')",
        "import numpy as p\nfrom pathlib import Path\n"
        "def f(p):\n    p.rename('b')\nf(Path('a'))",
    ],
)
def test_attempts_to_sneak_a_write_past_the_check_are_flagged(code):
    assert may_write_files(code) is True


def test_unparseable_or_hostile_source_counts_as_writing():
    assert may_write_files("x = 1\x00") is True
    assert may_write_files("(" * 10_000 + ")" * 10_000) is True


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
    code = f"open(r'{target}', 'w').write('x')"
    # Stands in for a write no static check could see.
    with patch("gaia_agent_chat.agent.may_write_files", return_value=False):
        (turn,) = _run(tools, tmp_path, "run_python", code=code)
    assert turn["incomplete"] is True
    assert str(target) in turn["untracked"][0]["paths"]


def test_a_scan_cut_short_counts_as_a_possible_write(tools, tmp_path):
    with patch(
        "gaia_agent_chat.python_jobs.files_written_since", return_value=([], False)
    ):
        (turn,) = _run(tools, tmp_path, "run_python", code="print(1)")
    assert turn["incomplete"] is True
    assert turn["untracked"][0]["may_write"] is True


def test_a_run_that_fails_part_way_counts_as_a_possible_write(tools, tmp_path):
    with (
        patch(
            "gaia_agent_chat.agent.run_python_job",
            side_effect=RuntimeError("interrupted"),
        ),
        fs.recording_turn(SID, root=tmp_path),
        pytest.raises(RuntimeError),
    ):
        tools["run_python"]["function"](code="print(1)")
    (turn,) = fs.list_turns(SID, root=tmp_path)
    assert turn["incomplete"] is True


def test_a_read_only_script_leaves_no_warning(tools, tmp_path, project):
    script = project / "count_errors.py"
    script.write_text(
        "lines = open('incidents.log').read().splitlines()\nprint(len(lines))\n"
    )
    assert _run(tools, tmp_path, "execute_python_file", file_path=str(script)) == []
