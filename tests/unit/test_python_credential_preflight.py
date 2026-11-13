# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Python code that names a credential file is refused before it runs.

``run_python`` and ``execute_python_file`` run arbitrary code, so the paths
that code spells out are held to ``PathValidator.is_read_blocked``. The refusal
comes from ``Agent._policy_refusal``, before any prompt, so a blanket
pre-approval (``auto_approve_gated_tools``) does not lift it.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.credential_reads import (
    credential_in_python,
    python_credential_refusal,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "project"
    root.mkdir(parents=True)
    (root / ".env").write_text("TOKEN=abc\n")
    (root / "secrets.json").write_text("{}\n")
    (root / "data.csv").write_text("a,b\n1,2\n")
    (home / ".npmrc").write_text("//registry/:_authToken=x\n")
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia_home"))
    monkeypatch.chdir(root)
    return root


class _PythonAgent(Agent):
    """A base agent whose Python tools only record that they ran."""

    ran: list = []

    def _get_system_prompt(self):
        return "probe"

    def _register_tools(self):
        ran = self.ran

        @tool
        def run_python(code: str) -> dict:
            """Run a Python snippet."""
            ran.append(code)
            return {"status": "success", "stdout": ""}

        @tool
        def execute_python_file(file_path: str) -> dict:
            """Run a Python file."""
            ran.append(file_path)
            return {"status": "success", "stdout": ""}


@pytest.fixture
def agent(project):
    saved = dict(_TOOL_REGISTRY)
    _PythonAgent.ran = []
    with patch("gaia.agents.base.agent.AgentSDK"):
        built = _PythonAgent(silent_mode=True, skip_lemonade=True)
    built.console.auto_approve_gated_tools = True
    try:
        yield built
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


REFUSED_SNIPPETS = [
    "print(open('secrets.json').read())",
    "from pathlib import Path\nprint(Path('~/.npmrc').expanduser().read_text())",
    pytest.param(
        "print(open(r'C:\\x\\.env').read())",
        marks=pytest.mark.skipif(os.name != "nt", reason="a Windows path"),
    ),
    "import os\nfd = os.open('x.pem', 0)",
    "print(open('.env').read())",
    "print(open('SECRETS.JSON').read())",
    "import os\nprint(open(os.path.join('config', '.netrc')).read())",
    "from pathlib import Path\nprint((Path.home() / '.npmrc').read_text())",
    "name = '.env'\nprint(open(name).read())",
    "print(open(f\"{'.'}env\").read())",
    "import subprocess\nsubprocess.run('cat .env', shell=True)",
    "import subprocess\nsubprocess.run(['type', 'secrets.json'])",
    "import os\nos.system('more .env')",
    "print(open('server.key', 'rb').read())",
    "import io\nprint(io.open('.env').read())",
    "from pathlib import Path\nwith Path('secrets.json').open() as f:\n    print(f.read())",
    "from pathlib import Path\np = Path('.env')\nprint(p.read_bytes())",
    "import shutil\nshutil.copy('.env', 'copy.txt')",
    "import shutil\nshutil.copyfile(src='secrets.json', dst='x')",
]


@pytest.mark.parametrize("code", REFUSED_SNIPPETS)
def test_run_python_naming_a_credential_file_is_refused(agent, code):
    result = agent._execute_tool("run_python", {"code": code})

    assert result["executed"] is False, result
    assert "credentials" in result["error"]
    assert agent.ran == []


@pytest.mark.parametrize(
    "code",
    [
        "print(open('data.csv').read())",
        "print('put your token in .env')",
        "print('x'.endswith('.pem'))",
        "import json\nprint(json.dumps({'key': 1}))",
        "print(open('secrets.md', 'w').write('notes'))",
        "print(1 + 1)",
        "print('Copy example.env to app/.env before you start')",
        "msg = 'edit config/.npmrc by hand'\nprint(msg)",
        "import os\nprint(os.path.exists('.env'))",
        "from pathlib import Path\nprint(Path('.env').exists())",
        "import subprocess\nsubprocess.run(['git', 'add', '.env'])",
        "import shutil\nshutil.copy('data.csv', '.env.example')",
        "import shutil\nshutil.move('scratch.txt', '.env')",
    ],
)
def test_ordinary_python_still_runs(agent, code):
    result = agent._execute_tool("run_python", {"code": code})

    assert result.get("status") == "success", result
    assert agent.ran == [code]


def test_a_script_that_reads_a_credential_file_is_refused(agent, project):
    script = project / "leak.py"
    script.write_text("print(open('secrets.json').read())\n")

    result = agent._execute_tool("execute_python_file", {"file_path": str(script)})

    assert result["executed"] is False, result
    assert agent.ran == []


def test_a_script_that_reads_data_still_runs(agent, project):
    script = project / "load.py"
    script.write_text("print(open('data.csv').read())\n")

    result = agent._execute_tool("execute_python_file", {"file_path": str(script)})

    assert result.get("status") == "success", result


def test_running_a_credential_file_as_a_script_is_refused(agent, project):
    result = agent._execute_tool("execute_python_file", {"file_path": ".env"})

    assert result["executed"] is False, result


def test_the_refusal_carries_no_approval_tier(project):
    refusal = python_credential_refusal(
        "run_python", {"code": "open('.env')"}, [str(project)]
    )

    assert refusal is not None
    assert "tier" not in refusal
    assert refusal["executed"] is False


def test_other_tools_are_not_checked(project):
    assert python_credential_refusal("read_file", {"code": "open('.env')"}, []) is None


def test_a_link_to_a_credential_file_is_refused(project, monkeypatch):
    (project / "notes.txt").write_text("innocent\n")
    real = os.path.realpath

    def fake_realpath(path, *args, **kwargs):
        if os.path.basename(os.fspath(path)) == "notes.txt":
            return str(project / ".env")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", fake_realpath)

    assert credential_in_python("open('notes.txt')", [str(project)]) is not None


def test_a_computed_path_is_beyond_the_check(project):
    """Documented limit: a path built only at run time is not seen."""
    code = "print(open(''.join(['.', 'e', 'n', 'v'])).read())"

    assert credential_in_python(code, [str(project)]) is None


@pytest.mark.parametrize(
    "code",
    [
        "open('.env', 'w').write('A=1')",
        "open('.env', mode='a').write('A=1')",
        "with open('.env', 'x') as f:\n    f.write('A=1')",
        "open('.env', 'wb').write(b'A=1')",
        "open('.env', 'w+').write('A=1')",
        "import io\nio.open('secrets.json', 'w').write('{}')",
        "from pathlib import Path\nPath('.env').open('w').write('A=1')",
        "from pathlib import Path\nPath('.env').open(mode='a').write('A=1')",
        "from pathlib import Path\nPath('.env').write_text('A=1')",
        "import os\nfd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_TRUNC)",
    ],
)
def test_opening_a_credential_file_to_write_still_runs(agent, code):
    result = agent._execute_tool("run_python", {"code": code})

    assert result.get("status") == "success", result
    assert agent.ran == [code]


@pytest.mark.parametrize(
    "code",
    [
        "print(open('.env', 'r').read())",
        "print(open('.env', 'rb').read())",
        "f = open('.env', 'a+')\nf.seek(0)\nprint(f.read())",
        "print(open('.env', 'r+').read())",
        "print(open('.env', mode='rt').read())",
        "mode = 'w'\nprint(open('.env', mode).read())",
        "from pathlib import Path\nprint(Path('.env').open('r').read())",
        "import os\nprint(os.read(os.open('.env', os.O_RDONLY), 99))",
        "import os\nprint(os.read(os.open('.env', os.O_RDWR), 99))",
    ],
)
def test_opening_a_credential_file_to_read_is_refused(agent, code):
    result = agent._execute_tool("run_python", {"code": code})

    assert result["executed"] is False, result
    assert agent.ran == []


def test_large_code_naming_a_credential_file_is_still_checked(project):
    code = "x = 1\n" * 20000 + "print(open('.env').read())\n"

    assert len(code) > 64 * 1024
    assert credential_in_python(code, [str(project)]) is not None


def test_large_code_without_a_read_is_not_parsed(project, monkeypatch):
    import gaia.agents.base.credential_reads as module

    def parse_fails(*_args, **_kwargs):
        raise AssertionError("parsed code that cannot read a file")

    monkeypatch.setattr(module, "python_file_reads", parse_fails)

    assert credential_in_python("x = 1\n" * 170000, [str(project)]) is None
    big_reader = "y = open('data.csv').read()\n" * 3000
    assert credential_in_python(big_reader, [str(project)]) is None


@pytest.mark.parametrize(
    "code",
    [
        "import subprocess\nprint(subprocess.check_output(['cat', '.env']))",
        "import subprocess\nprint(subprocess.getstatusoutput('cat .env'))",
        "import subprocess\nprint(subprocess.getoutput('cat secrets.json'))",
        "import subprocess\nsubprocess.check_call(['cat', '.env'])",
        "from subprocess import check_output as co\nprint(co(['cat', '.env']))",
    ],
)
def test_every_subprocess_read_is_refused(agent, code):
    result = agent._execute_tool("run_python", {"code": code})

    assert result["executed"] is False, result
    assert agent.ran == []


def test_every_read_call_the_check_knows_trips_the_quick_skip():
    from gaia.agents.base import credential_reads, denied_effects

    names = (
        denied_effects._SUBPROCESS_FUNCS
        | denied_effects._PATH_READERS
        | denied_effects._SHUTIL_READERS
        | {"open", "system", "popen"}
    )
    assert names <= denied_effects.PYTHON_READ_CALLS
    for name in names:
        code = f"x.{name}('.env')"
        assert credential_reads._may_read_credentials(code), name
