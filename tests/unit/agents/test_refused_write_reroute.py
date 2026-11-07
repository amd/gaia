# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A write the path sandbox refused stays refused through run_python (#4071).

Builds on the refused-delete ledger (#6882): once ``write_file`` is refused
for a path, Python or shell code that writes, moves, copies or renames onto
that path in the same turn is the same effect, and is refused without an
"Allow once" question. Classification only; nothing here runs the snippets.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import SilentConsole
from gaia.agents.base.denied_effects import PATH, DeniedEffects
from gaia.agents.base.tools import tool
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator

# ---------------------------------------------------------------------------
# Classification: which Python writes reach the refused path
# ---------------------------------------------------------------------------


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "outside"
    return {"dir": folder, "file": folder / "report.md", "root": tmp_path}


def _refused_write(target, cwd) -> DeniedEffects:
    """The ledger after the sandbox refused ``write_file(<target>)``."""
    ledger = DeniedEffects(str(cwd))
    ledger.record(
        "write_file",
        {"file_path": str(target), "content": "x"},
        f"Access denied: '{target}' is not in allowed paths.",
        kinds={PATH},
        refused=True,
    )
    assert ledger, "the refused write must leave its target on the ledger"
    return ledger


def _writing_snippets(folder, target, root):
    d, t = repr(str(folder)), repr(str(target))
    other = repr(str(root / "inside" / "draft.md"))
    parent, name = repr(str(folder.parent)), repr(folder.name)
    return [
        # Direct open-for-write, every mode and spelling.
        f"open({t}, 'w').write('x')",
        f"with open({t}, 'a') as f:\n    f.write('x')",
        f"with open({t}, mode='wb') as f:\n    f.write(b'x')",
        f"open({t}, 'x')",
        f"open({t}, 'r+')",
        f"open(file={t}, mode='w')",
        f"import io\nio.open({t}, 'w')",
        f"import codecs\ncodecs.open({t}, 'w', 'utf-8')",
        f"import os\nos.open({t}, os.O_WRONLY | os.O_CREAT)",
        # The shape in the issue trace: create the folder, then write the file.
        f"import os\nos.makedirs({d}, exist_ok=True)\n"
        f"with open(os.path.join({d}, 'report.md'), 'w') as f:\n    f.write('x')",
        f"import os\nos.mkdir({d})",
        # Aliasing and simple computed paths.
        f"p = {t}\nwith open(p, 'w') as f:\n    f.write('x')",
        f"import os\nout = os.path.join({d}, 'report.md')\nopen(out, 'w')",
        f"import os.path as osp\nopen(osp.join({d}, 'report.md'), 'w')",
        f"from os.path import join\nopen(join({d}, 'report.md'), 'w')",
        f"base = {d}\nopen(f'{{base}}/report.md', 'w')",
        f"base: str = {d}\nopen(base + '/report.md', 'w')",
        f"base, ext = {d}, '.md'\nopen(base + '/report' + ext, 'w')",
        f"import os\nopen(os.path.abspath({t}), 'w')",
        # pathlib.
        f"from pathlib import Path\nPath({t}).write_text('x')",
        f"from pathlib import Path\nPath({t}).write_bytes(b'x')",
        f"from pathlib import Path\n(Path({parent}) / {name} / 'report.md').write_text('x')",
        f"from pathlib import Path as P\np = P({d}) / 'report.md'\np.write_text('x')",
        f"import pathlib\npathlib.Path({t}).open('w').write('x')",
        f"from pathlib import Path\nPath({t}).touch()",
        f"from pathlib import Path\nPath({d}).mkdir(parents=True)",
        f"from pathlib import Path\np = Path({t})\np.parent.mkdir(parents=True)\n"
        "p.write_text('x')",
        f"from pathlib import Path\nPath({parent}).joinpath({name}, 'report.md')"
        ".write_text('x')",
        f"from pathlib import Path\nPath({other}).rename({t})",
        f"from pathlib import Path\nPath({other}).replace(Path({t}))",
        # Copy, move and rename onto the refused path.
        f"import shutil\nshutil.copy({other}, {t})",
        f"import shutil\nshutil.copy2({other}, {t})",
        f"import shutil\nshutil.copyfile({other}, {t})",
        f"import shutil\nshutil.copyfile(src={other}, dst={t})",
        f"import shutil\nshutil.copytree({repr(str(root / 'inside'))}, {d})",
        f"import shutil\nshutil.move({other}, {t})",
        f"import shutil as sh\nsh.copy({other}, {t})",
        f"from shutil import copyfile as cf\ncf({other}, {t})",
        f"import os\nos.rename({other}, {t})",
        f"import os\nos.replace({other}, {t})",
        f"from os import rename\nrename({other}, {t})",
    ]


def test_python_that_writes_a_refused_path_is_the_same_effect(outside):
    ledger = _refused_write(outside["file"], outside["root"])
    for code in _writing_snippets(outside["dir"], outside["file"], outside["root"]):
        assert ledger.conflict("run_python", {"code": code}) is not None, code


def test_a_script_file_that_writes_a_refused_path_is_the_same_effect(outside):
    ledger = _refused_write(outside["file"], outside["root"])
    snippets = _writing_snippets(outside["dir"], outside["file"], outside["root"])
    for i, code in enumerate(snippets):
        script = outside["root"] / f"make_report_{i}.py"
        script.write_text(code)
        assert (
            ledger.conflict("execute_python_file", {"file_path": str(script)})
            is not None
        ), code


def test_a_shell_write_to_a_refused_path_is_the_same_effect(outside):
    ledger = _refused_write(outside["file"], outside["root"])
    t, other = str(outside["file"]), str(outside["root"] / "draft.md")
    for command in [
        f'echo x > "{t}"',
        f'echo x >> "{t}"',
        f'cp "{other}" "{t}"',
        f'mv "{other}" "{t}"',
        f'Copy-Item "{other}" "{t}"',
        f'Set-Content -Path "{t}" -Value x',
        f'mkdir -p "{outside["dir"]}"',
        f"python -c \"open(r'{t}', 'w').write('x')\"",
    ]:
        assert (
            ledger.conflict("run_shell_command", {"command": command}) is not None
        ), command


def test_a_write_whose_target_the_code_computes_is_treated_as_overlapping(outside):
    """Fail closed after a refusal, as for deletes: it could be the refused path."""
    ledger = _refused_write(outside["file"], outside["root"])
    for code in [
        "import sys\nopen(sys.argv[1], 'w').write('x')",
        "import shutil, sys\nshutil.copy('a.txt', sys.argv[1])",
        "from pathlib import Path\nPath(input()).write_text('x')",
        f"open({str(outside['file'])!r}, input())",
    ]:
        assert ledger.conflict("run_python", {"code": code}) is not None, code


def test_python_that_reads_or_writes_elsewhere_is_not_swept_up(outside):
    ledger = _refused_write(outside["file"], outside["root"])
    t, d = repr(str(outside["file"])), repr(str(outside["dir"]))
    other = repr(str(outside["root"] / "inside" / "draft.md"))
    for code in [
        f"print(open({t}).read())",
        f"print(open({t}, 'r').read())",
        f"print(open({t}, mode='rb').read())",
        f"import io\nio.open({t}, 'r')",
        f"import os\nos.open({t}, os.O_RDONLY)",
        f"from pathlib import Path\nprint(Path({t}).read_text())",
        f"from pathlib import Path\nprint(Path({t}).open().read())",
        f"import os\nprint(os.path.exists({t}), os.listdir({d}))",
        f"open({other}, 'w').write('x')",
        f"from pathlib import Path\nPath({other}).write_text('x')",
        f"import shutil\nshutil.copy({t}, {other})",
        f"import os\nos.makedirs({repr(str(outside['root'] / 'inside'))})",
        f"import tarfile\ntarfile.open({other}, 'w')",
        f"s = {t}\nprint(s.replace('a', 'b'))",
        "import json\nprint(json.dumps({'a': 1}))",
    ]:
        assert ledger.conflict("run_python", {"code": code}) is None, code


def test_writing_the_report_somewhere_allowed_instead_still_runs(outside):
    """The recovery the refusal asks for: the report goes in the working folder."""
    ledger = _refused_write(outside["file"], outside["root"] / "project")
    for code in [
        "import os\nopen(os.path.join(os.getcwd(), 'report.md'), 'w').write('x')",
        "from pathlib import Path\n(Path.cwd() / 'report.md').write_text('x')",
        "import os, tempfile\n"
        "open(os.path.join(tempfile.gettempdir(), 'r.md'), 'w').write('x')",
        "with open('report.md', 'w') as f:\n    f.write('x')",
    ]:
        assert ledger.conflict("run_python", {"code": code}) is None, code


def test_nothing_is_blocked_before_a_refusal(outside):
    ledger = DeniedEffects(str(outside["root"]))
    code = f"open({str(outside['file'])!r}, 'w').write('x')"
    assert ledger.conflict("run_python", {"code": code}) is None


def test_a_computed_write_is_never_recorded_as_the_refused_one(tmp_path):
    """Declining such a snippet must not block every later write."""
    ledger = DeniedEffects(str(tmp_path))
    ledger.record(
        "run_python",
        {"code": "import sys\nopen(sys.argv[1], 'w').write('x')"},
        "Tool 'run_python' was denied by the user.",
    )
    assert (
        ledger.conflict("write_file", {"file_path": "notes.txt", "content": "x"})
        is None
    )


# ---------------------------------------------------------------------------
# The gate in Agent._execute_tool: a sandbox refusal lands on the ledger
# ---------------------------------------------------------------------------


class _Console(SilentConsole):
    """Pre-approves gated tools, as GAIA_AUTO_APPROVE_TOOLS=1 does."""

    def __init__(self, auto_approve=True):
        super().__init__()
        self.auto_approve_gated_tools = auto_approve
        self.prompted = []
        self.questions = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.prompted.append(tool_name)
        return True

    def request_user_input_blocking(self, message, choices=None, **_):
        self.questions.append(message)
        return Agent.ALLOW_DENIED_EFFECT


class _SandboxedAgent(Agent, FileIOToolsMixin):
    def __init__(self, scope, console):
        self.path_validator = PathValidator(allowed_paths=[str(scope)])
        self.ran = []
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(
                silent_mode=True, skip_lemonade=True, output_handler=console
            )

    def _get_system_prompt(self) -> str:
        return "sandboxed"

    def _register_tools(self) -> None:
        self.register_file_io_tools()
        agent = self

        @tool
        def run_python(code: str) -> dict:
            """Probe runner: records the call, runs nothing."""
            agent.ran.append(("run_python", code))
            return {"status": "success", "return_code": 0}

        @tool
        def run_shell_command(command: str) -> dict:
            """Probe shell: records the call, runs nothing."""
            agent.ran.append(("run_shell_command", command))
            return {"status": "success", "return_code": 0}


@pytest.fixture
def layout(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gaia.security._system_temp_roots", lambda: {str(tmp_path / "systemp")}
    )
    scope = tmp_path / "project"
    scope.mkdir()
    report_dir = tmp_path / "Documents" / "reports"
    return {"scope": scope, "dir": report_dir, "file": report_dir / "report.md"}


def _agent(layout, console):
    agent = _SandboxedAgent(layout["scope"], console)
    agent._denied_effects = DeniedEffects(str(layout["scope"]))
    return agent


def _issue_trace_code(layout):
    d = repr(str(layout["dir"]))
    return (
        f"import os\nos.makedirs({d}, exist_ok=True)\n"
        f"with open(os.path.join({d}, 'report.md'), 'w') as f:\n"
        "    f.write('# Report')\n"
    )


def test_an_unattended_refused_write_cannot_be_rerouted_through_run_python(layout):
    """The issue trace: write_file refused, then the same file from run_python."""
    console = _Console(auto_approve=True)
    agent = _agent(layout, console)

    refused = agent._execute_tool(
        "write_file", {"file_path": str(layout["file"]), "content": "# Report"}
    )
    rerouted = agent._execute_tool("run_python", {"code": _issue_trace_code(layout)})

    assert refused["status"] == "error"
    assert "not in allowed paths" in refused["error"]
    assert not layout["file"].exists()
    assert rerouted["status"] == "denied", rerouted
    assert rerouted["executed"] is False
    assert agent.ran == []
    # A refusal no approval could lift is never offered as "Allow once".
    assert console.questions == []
    assert "was refused" in rerouted["error"]


def test_a_refused_write_cannot_be_rerouted_through_the_shell(layout):
    console = _Console(auto_approve=True)
    agent = _agent(layout, console)
    agent._execute_tool(
        "write_file", {"file_path": str(layout["file"]), "content": "# Report"}
    )

    rerouted = agent._execute_tool(
        "run_shell_command", {"command": f'echo hi > "{layout["file"]}"'}
    )

    assert rerouted["status"] == "denied", rerouted
    assert agent.ran == []
    assert console.questions == []


def test_a_write_no_approval_could_grant_is_refused_through_run_python(
    layout, tmp_path
):
    """The tool's pre-flight refusal (never askable) lands on the ledger too."""
    target = tmp_path / "systemp" / "report.md"
    console = _Console(auto_approve=False)
    agent = _agent(layout, console)

    refused = agent._execute_tool(
        "write_file", {"file_path": str(target), "content": "x"}
    )
    rerouted = agent._execute_tool(
        "run_python", {"code": f"open({str(target)!r}, 'w').write('x')"}
    )

    assert refused["status"] == "error"
    assert rerouted["status"] == "denied", rerouted
    assert agent.ran == []
    assert console.questions == []


def test_a_write_the_sandbox_allows_records_nothing(layout):
    console = _Console(auto_approve=True)
    agent = _agent(layout, console)
    inside = layout["scope"] / "report.md"

    written = agent._execute_tool(
        "write_file", {"file_path": str(inside), "content": "x"}
    )
    after = agent._execute_tool(
        "run_python", {"code": f"open({str(inside)!r}, 'a').write('y')"}
    )

    assert written["status"] == "success", written
    assert not agent._denied_effects
    assert after["status"] == "success"


def test_other_writes_still_run_after_a_refusal(layout):
    console = _Console(auto_approve=True)
    agent = _agent(layout, console)
    agent._execute_tool(
        "write_file", {"file_path": str(layout["file"]), "content": "x"}
    )
    inside = repr(str(layout["scope"] / "report.md"))

    result = agent._execute_tool(
        "run_python", {"code": f"open({inside}, 'w').write('x')"}
    )

    assert result["status"] == "success"
    assert agent.ran == [("run_python", f"open({inside}, 'w').write('x')")]


def test_a_read_first_refusal_inside_scope_records_nothing(layout):
    """Only the sandbox's no is final; "read it first" is a step to take."""
    console = _Console(auto_approve=True)
    agent = _agent(layout, console)
    existing = layout["scope"] / "notes.md"
    existing.write_text("old\n", encoding="utf-8")

    refused = agent._execute_tool(
        "write_file", {"file_path": str(existing), "content": "new"}
    )

    assert refused["status"] == "error", refused
    assert not agent._denied_effects
