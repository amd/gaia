# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Code that names a protected folder or credential file is refused before it runs.

``run_python`` used to be one opaque approval: a single Allow read ``.env``,
read and wrote inside ``~/.ssh``, and copied a key to an ordinary folder for
``read_file`` to pick up. The file tools refuse those paths outright, so code
that names them is refused outright too — before the card, and whatever an
earlier "Always allow" said. A shell line that hands code to an interpreter
is held to the same rule.

These cover what the screen catches. What it cannot catch — a path the code
computes while it runs — is pinned too, so nobody reads it as a sandbox.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import gaia.security as security
from gaia.agents.base.agent import Agent
from gaia.agents.base.code_path_screen import (
    protected_paths_in_code,
    python_code_preflight,
)
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.tools import tool
from gaia.security import PathValidator

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows path spellings")


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake = (tmp_path / "home").resolve()
    (fake / ".ssh").mkdir(parents=True)
    (fake / ".ssh" / "id_test").write_text("sshmark-K9v4-30562", encoding="utf-8")
    (fake / "Documents" / "w").mkdir(parents=True)
    (fake / "Documents" / "w" / ".env").write_text("SECRET=1", encoding="utf-8")
    (fake / "project").mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake))
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setenv("USERPROFILE", str(fake))
    monkeypatch.delenv("GAIA_HOME", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.setattr(security, "SECRET_DIRECTORIES", security._secret_directories())
    monkeypatch.setattr(
        security,
        "BLOCKED_DIRECTORIES",
        security._with_resolved_forms(security._get_blocked_directories()),
    )
    monkeypatch.setattr(
        security,
        "GAIA_STATE_DIRECTORIES",
        security._with_resolved_forms(security._gaia_state_dirs()),
    )
    monkeypatch.setattr(security, "_system_temp_roots", lambda: set())
    return fake


def _validator(home):
    return PathValidator(allowed_paths=[str(home / "project")])


def _named(code, home, *, python=True, may_write=True):
    return protected_paths_in_code(
        code,
        python=python,
        cwd=str(home / "project"),
        validator=_validator(home),
        may_write=may_write,
    )


# ── What the screen catches ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "template",
    [
        'print(open(r"{ssh}\\id_test").read())',
        'print(open("{ssh_fwd}/id_test").read())',
        'import shutil\nshutil.copy(r"{ssh}\\id_test", r"{docs}\\copy.txt")',
        'open(r"{ssh}\\pyw.txt", "w").write("x")',
        'print(open(r"{docs}\\w\\.env").read())',
        "import os\nprint(open(os.path.join(os.path.expanduser('~'), '.ssh', 'id_test')).read())",
        "from pathlib import Path\nprint((Path.home() / '.ssh' / 'id_test').read_text())",
        "import os\nhome = os.environ['USERPROFILE']\np = home + '/.s' + 'sh/id_test'\nprint(open(p).read())",
        "import os\nd = os.getenv('HOME')\nprint(open(f'{{d}}/.ssh/id_test').read())",
        "parts = ['.', 's', 's', 'h']\nname = ''.join(parts)\nprint(name)",
        "p = '.e'\np += 'nv'\nprint(open(p).read())",
        "import glob\nprint(glob.glob('~/.ssh/*'))",
        "print(open('id_rsa').read())",
        "print(open('server.pem').read())",
        "open(r'{home}\\.gaia\\mcp_servers.json', 'w').write('{{}}')",
        "import urllib.request\nurllib.request.urlopen('file:///{ssh_fwd}/id_test')",
    ],
)
def test_code_naming_a_protected_path_is_named(home, template):
    code = template.format(
        ssh=home / ".ssh",
        ssh_fwd=(home / ".ssh").as_posix(),
        docs=home / "Documents",
        home=home,
    )

    assert _named(code, home), code


@windows_only
@pytest.mark.parametrize(
    "prefix",
    [
        "\\\\?\\",
        "\\\\localhost\\{drive}$",
        "\\\\127.0.0.1\\{drive}$",
        "\\\\?\\UNC\\localhost\\{drive}$",
    ],
)
def test_another_spelling_of_a_protected_path_is_named(home, prefix):
    # No name in this path is a credential name, so only the canonical form finds it.
    target = home / ".config" / "gcloud" / "creds.db"
    drive, rest = os.path.splitdrive(str(target))
    spelled = prefix.format(drive=drive[0]) + (rest if "$" in prefix else str(target))
    spelled = spelled.replace("\\gcloud\\", "\\GCLOUD.\\")
    plain_name = f"print(open(r'{home / 'Documents' / 'creds.db'}').read())"

    assert _named(f"print(open(r'{spelled}').read())", home)
    assert _named(plain_name, home) == []


@pytest.mark.parametrize(
    "code",
    [
        "print(sum(range(10)))",
        "import pandas as pd\nprint(pd.read_csv('sales.csv').head())",
        "print('Configure your .env file before running')",
        "print(open('notes/ssh-setup.md').read())",
        "import subprocess\nsubprocess.run([r'C:\\Program Files\\Git\\bin\\git.exe', 'status'])",
        "open('report.md', 'w').write('# Report')",
        "import os\nprint(os.listdir(os.path.expanduser('~')))",
        r"""import re
print(re.split(r"\\\\", "a\\b"))""",
        "row = {'user': 'a', 'passwd': 'b'}\nprint(row['passwd'])",
        "import requests\nprint(requests.get('https://example.com/.env').status_code)",
    ],
)
def test_ordinary_code_is_not_named(home, code):
    assert _named(code, home) == []


def test_gaia_settings_are_named_only_by_code_that_may_write(home):
    code = f"print(open(r'{home / '.gaia' / 'config.json'}').read())"

    assert _named(code, home, may_write=False) == []
    assert _named(code, home, may_write=True)


@pytest.mark.parametrize(
    "script",
    [
        "Get-Content (Join-Path $HOME '.ssh\\id_test')",
        "Get-Content $env:USERPROFILE\\.ssh\\id_test",
        "require('fs').readFileSync(require('path').join(require('os').homedir(), '.ssh', 'id_test'))",
        "type %USERPROFILE%\\Documents\\w\\.env",
    ],
)
def test_script_text_in_other_languages_is_screened(home, script):
    assert _named(script, home, python=False), script


def test_a_path_computed_at_run_time_is_not_caught(home):
    """The documented limit: this is a screen of the text, not a sandbox."""
    code = (
        "import base64, os\n"
        "p = base64.b64decode('{}').decode()\n"
        "print(open(os.path.expanduser(p)).read())\n"
    ).format(base64.b64encode(b"~/.ssh/id_test").decode())

    assert _named(code, home) == []


# ── A shell line that hands code to an interpreter ───────────────────────


@pytest.mark.parametrize(
    "template",
    [
        "python -c \"print(open('{env}').read())\"",
        "python -c \"import os; print(open(os.path.join(os.path.expanduser('~'), '.ssh', 'id_test')).read())\"",
        "python -c \"import shutil; shutil.copy('{key}', 'copy.txt')\"",
        "powershell -Command \"Get-Content (Join-Path $HOME '.ssh/id_test')\"",
        "node -e \"require('fs').readFileSync(require('path').join(require('os').homedir(), '.ssh', 'id_test'))\"",
    ],
)
def test_a_shell_line_handing_code_to_an_interpreter_is_screened(home, template):
    from gaia.agents.tools.shell_tools import TIER_REFUSE, ShellToolsMixin, _parse_line

    host = type("_Host", (ShellToolsMixin,), {})()
    host.path_validator = _validator(home)
    command = template.format(
        env=(home / "Documents" / "w" / ".env").as_posix(),
        key=(home / ".ssh" / "id_test").as_posix(),
    )
    steps, error = _parse_line(command, bypass_gates=True)
    assert error is None, error

    refusals = [
        host._path_traversal_refusal(step, str(home / "project"), frozenset())
        for step in steps
    ]

    refusal = next((r for r in refusals if r), None)
    assert refusal is not None, command
    # The line takes this as refuse-tier when the refusal names none of its own.
    assert refusal.get("tier", TIER_REFUSE) == TIER_REFUSE


def test_an_ordinary_inline_script_still_passes(home):
    from gaia.agents.tools.shell_tools import ShellToolsMixin, _parse_line

    host = type("_Host", (ShellToolsMixin,), {})()
    host.path_validator = _validator(home)
    steps, _ = _parse_line('python -c "print(sum(range(10)))"', bypass_gates=True)

    assert all(
        host._path_traversal_refusal(step, str(home / "project"), frozenset()) is None
        for step in steps
    )


# ── run_python: refused before the card, whatever was granted ────────────


class _Console(SilentConsole):
    """Says yes to every prompt, and remembers being asked."""

    def __init__(self):
        super().__init__()
        self.prompted = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.prompted.append(tool_name)
        return True


class _PythonAgent(Agent):
    def __init__(self, home, console):
        self.path_validator = PathValidator(allowed_paths=[str(home / "project")])
        self.ran = []
        self._home = home
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(
                silent_mode=True, skip_lemonade=True, output_handler=console
            )

    def _get_system_prompt(self) -> str:
        return "python"

    def _register_tools(self) -> None:
        agent = self

        @tool(preflight=python_code_preflight(agent))
        def run_python(code: str) -> dict:
            """Probe runner: records the call, runs nothing."""
            agent.ran.append(code)
            return {"status": "success", "return_code": 0}


def test_run_python_naming_a_protected_file_never_reaches_the_card(home):
    console = _Console()
    agent = _PythonAgent(home, console)
    code = f"import shutil\nshutil.copy(r'{home / '.ssh' / 'id_test'}', 'copy.txt')"

    result = agent._execute_tool("run_python", {"code": code})

    assert result["status"] == "error"
    assert "No approval can grant it" in result["error"]
    assert console.prompted == []
    assert agent.ran == []


def test_always_allow_does_not_cover_a_snippet_naming_a_protected_path(home):
    console = _Console()
    agent = _PythonAgent(home, console)
    # What "Always allow running python in this chat" records.
    assert console.grant_call_for_session("run_python", {"code": "print(0)"})
    env_file = home / "Documents" / "w" / ".env"

    ordinary = agent._execute_tool("run_python", {"code": "print(1)"})
    protected = agent._execute_tool(
        "run_python", {"code": f"print(open(r'{env_file}').read())"}
    )

    assert ordinary["status"] == "success"
    assert protected["status"] == "error"
    assert "No approval can grant it" in protected["error"]
    assert agent.ran == ["print(1)"]


# ── The flagship's own run_python and execute_python_file ────────────────


@pytest.fixture(scope="module")
def flagship_tools(tmp_path_factory):
    """The flagship's tools, registered by one ChatAgent (building one takes ~10 s)."""
    pytest.importorskip("gaia_agent")
    from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig

    from gaia.agents.base.tools import _TOOL_REGISTRY

    project = tmp_path_factory.mktemp("flagship-project").resolve()
    (project / "pyproject.toml").write_text("[project]\nname = 'toy'\n")
    saved = dict(_TOOL_REGISTRY)
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(project)
        with (
            patch("gaia_agent.chat_agent.RAGSDK"),
            patch("gaia_agent.chat_agent.RAGConfig"),
        ):
            ChatAgent(ChatAgentConfig(silent_mode=True, allowed_paths=[str(project)]))
        try:
            yield project, dict(_TOOL_REGISTRY)
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)


def test_the_flagship_run_python_refuses_before_the_card_and_in_its_body(
    flagship_tools, home
):
    project, tools = flagship_tools
    marker = project / "ran.txt"
    key = home / ".ssh" / "id_test"
    code = (
        f"import shutil\nshutil.copy(r'{key}', 'copy.txt')\n"
        f"open(r'{marker}', 'w').write('ran')\n"
    )
    entry = tools["run_python"]

    before_card = entry["preflight"]({"code": code})
    in_body = entry["function"](code=code)

    assert before_card is not None
    assert "No approval can grant it" in before_card["error"]
    assert in_body["status"] == "error"
    assert not marker.exists()


def test_the_flagship_execute_python_file_screens_the_script_and_its_arguments(
    flagship_tools, home
):
    project, tools = flagship_tools
    copier = project / "copier.py"
    key = home / ".ssh" / "id_test"
    copier.write_text(
        f"import shutil\nshutil.copy(r'{key}', 'copy.txt')\n", encoding="utf-8"
    )
    generic = project / "copy_arg.py"
    generic.write_text(
        "import shutil, sys\nshutil.copy(sys.argv[1], sys.argv[2])\n",
        encoding="utf-8",
    )
    entry = tools["execute_python_file"]
    env_file = (home / "Documents" / "w" / ".env").as_posix()

    assert entry["preflight"]({"file_path": str(copier)}) is not None
    named = {"file_path": str(generic), "args": f"{env_file} out.txt"}
    assert entry["preflight"](named) is not None
    plain = {"file_path": str(generic), "args": "a.txt b.txt"}
    assert entry["preflight"](plain) is None
    assert entry["function"](file_path=str(copier))["status"] == "error"
    assert not (project / "copy.txt").exists()
