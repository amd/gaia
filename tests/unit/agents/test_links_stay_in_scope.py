# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Links can't carry the agent outside its allowed folders.

A refused file-changing command points at the file tools, never at
``run_python``; making a link is final even unattended; Python code is held to
the allowed folders after its paths are resolved; and a block on a real path
covers every link to it. Classification only: no link is created here, and
``os.path.realpath`` is faked wherever a link would be followed.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.denied_effects import (
    PATH,
    UNATTENDED_REFUSAL,
    DeniedEffects,
    _norm_path,
    creates_a_link,
    python_link_targets,
)
from gaia.agents.base.tools import tool
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.security import PathValidator

PROTECTED = (
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "drivers", "etc")
    if os.name == "nt"
    else "/etc"
)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for var in ("HOME", "USERPROFILE", "GAIA_HOME"):
        monkeypatch.setenv(var, str(home))
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:1")
    systemp = tmp_path / "systemp"
    systemp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systemp))
    monkeypatch.setattr("gaia.security._system_temp_roots", lambda: {str(systemp)})
    project = tmp_path / "project"
    project.mkdir()
    (project / "data.csv").write_text("a,b\n1,2\n")
    monkeypatch.chdir(project)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return {"project": project, "elsewhere": elsewhere, "systemp": systemp}


def _links(monkeypatch, links):
    """Make ``os.path.realpath`` follow *links* ({link path: target}) only."""
    real = os.path.realpath

    def fake(path, *args, **kwargs):
        text = os.path.abspath(os.fspath(path))
        folded = os.path.normcase(text)
        for link, target in links.items():
            at = os.path.normcase(os.path.abspath(str(link)))
            if folded == at or folded.startswith(at + os.sep):
                return str(target) + text[len(at) :]
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", fake)


class _Agent(Agent, ShellToolsMixin):
    scope = None

    def __init__(self, console):
        self.path_validator = PathValidator(allowed_paths=[str(self.scope)])
        self._console_override = console
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(silent_mode=True, skip_lemonade=True)

    def _get_system_prompt(self):
        return "probe"

    def _create_console(self):
        return self._console_override

    def _register_tools(self):
        self.register_shell_tools()
        agent = self
        agent.ran = []

        @tool
        def run_python(code: str) -> dict:
            """Probe Python runner."""
            agent.ran.append(("run_python", code))
            return {"status": "success"}

        @tool
        def execute_python_file(file_path: str) -> dict:
            """Probe script runner."""
            agent.ran.append(("execute_python_file", file_path))
            return {"status": "success"}

        @tool
        def read_file(file_path: str) -> dict:
            """Probe reader."""
            agent.ran.append(("read_file", file_path))
            return {"status": "success"}

        @tool
        def browse_directory(path: str = ".") -> dict:
            """Probe lister."""
            agent.ran.append(("browse_directory", path))
            return {"status": "success"}

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Probe writer."""
            agent.ran.append(("write_file", file_path))
            return {"status": "success"}

        @tool
        def edit_file(file_path: str, old: str, new: str) -> dict:
            """Probe editor."""
            agent.ran.append(("edit_file", file_path))
            return {"status": "success"}


def _agent(layout, console=None, full_access=False):
    console = console or SilentConsole(auto_approve_gated_tools=True)
    if full_access:
        console.full_access = True
    _Agent.scope = layout["project"]
    agent = _Agent(console)
    agent.max_commands_per_minute = 1000
    agent.max_commands_per_10_seconds = 1000
    agent._denied_effects = DeniedEffects(str(layout["project"]))
    return agent


def _shell(agent, command):
    return agent._execute_tool("run_shell_command", {"command": command})


def _python(agent, code):
    return agent._execute_tool("run_python", {"code": code})


# ---------------------------------------------------------------------------
# Unattended refusals point file changes at the file tools
# ---------------------------------------------------------------------------


def _link_commands(target):
    return [
        f'New-Item -ItemType Junction -Path link -Target "{target}"',
        f'New-Item -ItemType SymbolicLink -Path link -Value "{target}"',
        f'ni -it HardLink link -Target "{target}"',
        f'powershell -Command "New-Item -ItemType Junction -Path link -Target {target}"',
        f'mklink /J link "{target}"',
        f'mklink /D link "{target}"',
        f'mklink /H link "{target}"',
        f'cmd /c mklink /J link "{target}"',
        f'ln -s "{target}" link',
        f'ln "{target}" link',
        f"python -c \"import os; os.symlink(r'{target}', 'link')\"",
    ]


@pytest.mark.parametrize("index", range(len(_link_commands("x"))))
def test_an_unattended_link_refusal_offers_no_other_route(layout, index):
    agent = _agent(layout)
    command = _link_commands(layout["elsewhere"])[index]

    refused = _shell(agent, command)

    assert refused["executed"] is False
    # Final: a link is not something another tool may make instead.
    assert UNATTENDED_REFUSAL not in refused
    assert "run_python" not in refused["error"]
    rerouted = _python(
        agent, f"import os\nos.symlink({str(layout['elsewhere'])!r}, 'link')"
    )
    assert rerouted["executed"] is False
    assert agent.ran == []


@pytest.mark.parametrize(
    "command",
    [
        "mklink /J a b",
        "MKLINK.EXE /D a b",
        "cmd /c mklink a b",
        "New-Item -ItemType Junction -Path a -Target b",
        "New-Item -Type SymbolicLink a -Value b",
        "New-Item -ItemType:HardLink a -Target b",
        "ni -i junction a -Target b",
        "ln -s a b",
        "ln a b",
        "sudo ln -sf a b",
        "fsutil hardlink create a b",
        "bash -c 'ln -s a b'",
        "python -c \"import os; os.symlink('a', 'b')\"",
        "python -c \"import os; os.link('a', 'b')\"",
        "python -c \"import _winapi; _winapi.CreateJunction('a', 'b')\"",
        "python -c \"from pathlib import Path; Path('b').symlink_to('a')\"",
        "python -c \"from pathlib import Path; Path('b').hardlink_to('a')\"",
    ],
)
def test_every_spelling_of_a_link_is_recognised(command, tmp_path):
    assert creates_a_link(command, str(tmp_path))


@pytest.mark.parametrize(
    "command",
    [
        "New-Item -ItemType File a",
        "New-Item -ItemType Directory a",
        "Copy-Item a b",
        "echo link",
        "dir",
        "python -c \"print('symlink')\"",
    ],
)
def test_ordinary_commands_are_not_links(command, tmp_path):
    assert not creates_a_link(command, str(tmp_path))


@pytest.mark.parametrize(
    "command", ["Copy-Item a.txt b.txt", "Move-Item a.txt b.txt", "echo hi > out.txt"]
)
def test_a_file_changing_refusal_points_to_the_file_tools(layout, command):
    agent = _agent(layout)

    refused = _shell(agent, command)

    assert refused[UNATTENDED_REFUSAL] is True
    assert "write_file or edit_file" in refused["error"]
    assert "run_python" not in refused["error"]


def test_a_read_refusal_points_to_the_readers_not_python(layout):
    agent = _agent(layout)

    error = _shell(agent, "type notes.txt")["error"]

    assert "read_file" in error and "browse_directory" in error
    assert "run_python" not in error


def test_a_computation_refusal_still_offers_run_python(layout):
    agent = _agent(layout)

    error = _shell(agent, "Get-Process")["error"]

    assert "run_python to compute" in error


# ---------------------------------------------------------------------------
# Hard-blocked targets are final, and their refusal says so
# ---------------------------------------------------------------------------

_OFFERS = ("full access", "interactively", "approve it", "Agent UI")


@pytest.mark.parametrize(
    "command",
    [
        "curl http://169.254.169.254/latest/meta-data/",
        "curl http://metadata.google.internal/computeMetadata/v1/",
        f'copy a.txt "{os.path.join(PROTECTED, "x.txt")}"',
        f'type "{os.path.join(PROTECTED, "hosts")}"',
    ],
)
def test_a_hard_blocked_target_is_not_offered_through_approval(layout, command):
    agent = _agent(layout)

    refused = _shell(agent, command)

    assert refused["executed"] is False
    assert UNATTENDED_REFUSAL not in refused
    for offer in _OFFERS:
        assert offer not in refused["error"], refused["error"]
    assert "run_python" not in refused["error"]


def test_a_metadata_refusal_with_no_console_offers_nothing(layout):
    agent = _agent(layout)
    agent.console = None

    error = agent._blanket_approval_refusal(
        {"error": "needs approval", "tier": "confirm"},
        "curl http://169.254.169.254/latest/meta-data/",
    )["error"]

    for offer in _OFFERS:
        assert offer not in error


# ---------------------------------------------------------------------------
# Python code is held to the allowed folders, links followed
# ---------------------------------------------------------------------------

_LINK_SNIPPETS = [
    "import os\nos.symlink({t!r}, 'link')",
    "import os\nos.symlink(src={t!r}, dst='link')",
    "import os\nos.link({t!r}, 'link')",
    "from os import symlink as s\ns({t!r}, 'link')",
    "import os as o\no.symlink({t!r}, 'link', target_is_directory=True)",
    "import _winapi\n_winapi.CreateJunction({t!r}, 'link')",
    "from _winapi import CreateJunction\nCreateJunction({t!r}, 'link')",
    "from pathlib import Path\nPath('link').symlink_to({t!r})",
    "from pathlib import Path\nPath('link').hardlink_to({t!r})",
    "import pathlib\npathlib.Path('link').symlink_to(target={t!r})",
    "target = {t!r}\nimport os\nos.symlink(target, 'link')",
    "import subprocess\nsubprocess.run(['cmd', '/c', 'mklink', '/J', 'link', {t!r}])",
    "import os\nos.system('mklink /J link \"' + {t!r} + '\"')",
    "import subprocess\nsubprocess.run(['ln', '-s', {t!r}, 'link'])",
]


@pytest.mark.parametrize("snippet", _LINK_SNIPPETS)
@pytest.mark.parametrize("where", ["protected", "elsewhere"])
def test_python_making_a_link_out_of_scope_is_refused(layout, snippet, where):
    agent = _agent(layout)
    target = PROTECTED if where == "protected" else str(layout["elsewhere"])

    refused = _python(agent, snippet.format(t=target))

    assert refused["status"] == "error", refused
    assert refused["executed"] is False
    assert agent.ran == []


def test_a_relative_symlink_climbing_out_is_refused(layout):
    agent = _agent(layout)

    refused = _python(agent, "import os\nos.symlink('../elsewhere', 'link')")

    assert refused["status"] == "error", refused
    assert agent.ran == []


def test_a_link_whose_target_is_computed_is_refused(layout):
    agent = _agent(layout)

    refused = _python(agent, "import os\nos.symlink(input(), 'link')")

    assert refused["status"] == "error"
    assert "can't be checked" in refused["error"]
    assert agent.ran == []


def test_a_link_inside_the_allowed_folder_runs(layout):
    agent = _agent(layout)

    ran = _python(agent, "import os\nos.symlink('data.csv', 'alias.csv')")

    assert ran["status"] == "success", ran
    assert len(agent.ran) == 1


_READS_THROUGH_LINK = [
    "print(open('link/hosts').read())",
    "with open('link/hosts', encoding='utf-8') as f:\n    print(f.read())",
    "from pathlib import Path\nprint(Path('link/hosts').read_text())",
    "from pathlib import Path\nprint((Path('link') / 'hosts').read_text())",
    "import os\nprint(os.listdir('link'))",
    "from pathlib import Path\nprint(list(Path('link').iterdir()))",
    "import glob\nprint(glob.glob('link/*'))",
    "import shutil\nshutil.copy('link/hosts', 'copy.txt')",
    "import subprocess\nsubprocess.run(['cat', 'link/hosts'])",
    "import os\nos.system('cat link/hosts')",
]


@pytest.mark.parametrize("code", _READS_THROUGH_LINK)
def test_reading_through_a_link_to_a_protected_folder_is_refused(
    layout, monkeypatch, code
):
    _links(monkeypatch, {layout["project"] / "link": PROTECTED})
    agent = _agent(layout)

    refused = _python(agent, code)

    assert refused["status"] == "error", refused
    assert refused["executed"] is False
    assert "no approval or full access lifts this" in refused["error"]
    # Final: no tool is offered as another way in.
    assert "read_file" not in refused["error"]
    assert agent.ran == []


@pytest.mark.parametrize("code", _READS_THROUGH_LINK[:6])
def test_reading_through_a_link_elsewhere_points_to_the_readers(
    layout, monkeypatch, code
):
    _links(monkeypatch, {layout["project"] / "link": layout["elsewhere"]})
    agent = _agent(layout)

    refused = _python(agent, code)

    assert refused["status"] == "error", refused
    assert "read_file" in refused["error"]
    assert "leads to" in refused["error"]
    assert agent.ran == []


@pytest.mark.parametrize(
    "code",
    [
        "open('link/out.txt', 'w').write('x')",
        "from pathlib import Path\nPath('link/out.txt').write_text('x')",
        "import shutil\nshutil.copy('data.csv', 'link/data.csv')",
        "import os\nos.makedirs('link/new')",
    ],
)
def test_writing_through_a_link_elsewhere_points_to_the_writers(
    layout, monkeypatch, code
):
    _links(monkeypatch, {layout["project"] / "link": layout["elsewhere"]})
    agent = _agent(layout)

    refused = _python(agent, code)

    assert refused["status"] == "error", refused
    assert "write_file or edit_file" in refused["error"]
    assert "run_python" not in refused["error"]
    assert agent.ran == []


def test_full_access_reaches_elsewhere_but_never_a_protected_folder(
    layout, monkeypatch
):
    _links(
        monkeypatch,
        {
            layout["project"] / "link": layout["elsewhere"],
            layout["project"] / "sys": PROTECTED,
        },
    )
    agent = _agent(layout, full_access=True)

    allowed = _python(agent, "print(open('link/notes.txt').read())")
    refused = _python(agent, "print(open('sys/hosts').read())")

    assert allowed["status"] == "success", allowed
    assert refused["status"] == "error", refused
    assert len(agent.ran) == 1


def test_a_path_that_cannot_be_resolved_is_refused(layout, monkeypatch):
    real = os.path.realpath

    def broken(path, *args, **kwargs):
        if "unreadable" in os.fspath(path):
            raise OSError("access is denied")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "realpath", broken)
    agent = _agent(layout)

    refused = _python(agent, "print(open('unreadable/x.txt').read())")

    assert refused["status"] == "error", refused
    assert "could not be checked" in refused["error"]
    assert agent.ran == []


def test_execute_python_file_reading_through_a_link_is_refused(layout, monkeypatch):
    script = layout["project"] / "peek.py"
    script.write_text("print(open('link/hosts').read())\n")
    _links(monkeypatch, {layout["project"] / "link": PROTECTED})
    agent = _agent(layout)

    refused = agent._execute_tool("execute_python_file", {"file_path": str(script)})

    assert refused["status"] == "error", refused
    assert agent.ran == []


@pytest.mark.parametrize(
    "code",
    [
        "print(sum(range(10)))",
        "import json\nprint(json.dumps({'a': 1}))",
        "import math\nprint(math.sqrt(2))",
        "print(open('data.csv').read())",
        "import csv\nwith open('data.csv', newline='') as f:\n    print(list(csv.reader(f)))",
        "open('out.csv', 'w').write('x')",
        "from pathlib import Path\nPath('report.md').write_text('# Report')",
        "import os\nprint(os.listdir('.'))",
        "import os\nos.makedirs('charts', exist_ok=True)",
        "import os, tempfile\nopen(os.path.join(tempfile.gettempdir(), 'p.png'), 'wb')",
        "import os\nfor name in os.listdir('.'):\n    print(open(name).read())",
    ],
)
def test_computation_and_in_scope_files_still_run(layout, code):
    agent = _agent(layout)

    ran = _python(agent, code)

    assert ran["status"] == "success", ran
    assert len(agent.ran) == 1


def test_link_targets_are_read_where_the_os_reads_them(tmp_path):
    cwd = str(tmp_path)
    code = (
        "import os\n"
        "os.symlink('../up', 'sub/link')\n"
        "os.link('a.txt', 'sub/b.txt')\n"
        "os.symlink(name, 'x')\n"
    )

    targets = python_link_targets(code, cwd)

    # A symlink's relative target starts at the link's folder; a hard link's at cwd.
    assert targets == [
        os.path.join(cwd, "sub", "../up"),
        os.path.join(cwd, "a.txt"),
        None,
    ]


# ---------------------------------------------------------------------------
# A block on a real path covers every link to it
# ---------------------------------------------------------------------------


def test_a_block_on_the_real_path_covers_a_link_to_it(layout, monkeypatch):
    project, elsewhere = layout["project"], layout["elsewhere"]
    _links(monkeypatch, {project / "link": elsewhere})
    ledger = DeniedEffects(str(project))
    ledger.record(
        "write_file",
        {"file_path": str(elsewhere / "report.md"), "content": "x"},
        "refused",
        kinds={PATH},
        refused=True,
    )

    assert _norm_path("link/report.md", str(project)) == _norm_path(
        str(elsewhere / "report.md"), str(project)
    )
    assert ledger.conflict("run_python", {"code": "open('link/report.md', 'w')"})
    assert ledger.conflict(
        "run_shell_command", {"command": "Copy-Item data.csv link/report.md"}
    )


def test_a_block_through_a_link_covers_the_real_path(layout, monkeypatch):
    project, elsewhere = layout["project"], layout["elsewhere"]
    _links(monkeypatch, {project / "link": elsewhere})
    ledger = DeniedEffects(str(project))
    ledger.record(
        "write_file",
        {"file_path": "link/report.md", "content": "x"},
        "refused",
        kinds={PATH},
        refused=True,
    )

    assert ledger.conflict(
        "run_python", {"code": f"open({str(elsewhere / 'report.md')!r}, 'w')"}
    )
