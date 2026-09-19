# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An edit tool refuses a file the agent hasn't read, or one that changed since.

Benchmark runs caught the flagship patching files it had never opened, working
from a grep snippet or a guess, and one such refactor silently widened
behaviour. Every file-changing tool now checks the agent's own record of what it
has read: an existing file that isn't in it, or whose mtime/size moved since the
read, is refused before anything is written. Creating a file needs no read, and
what the agent itself wrote or edited stays unlocked.

The record lives on the agent instance, so these tests build real hosts — bare
mixins for the tool bodies, a real ``Agent`` for the check that runs before the
confirmation prompt.

No LLM or external service required.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.verification import check_was_executed
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.security import PathValidator

SAMPLE = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"


class _Host(FileIOToolsMixin, FileSearchToolsMixin, FileSystemToolsMixin):
    """One agent's worth of file tools, without the agent around them."""


def _capture(host, registrar):
    """Register one mixin's tools and hand back just those, registry restored.

    ``file_io_tools`` and ``file_tools`` both register ``read_file``,
    ``write_file`` and ``edit_file``, so each set is captured on its own.
    """
    saved = dict(_TOOL_REGISTRY)
    try:
        getattr(host, registrar)()
        return {
            name: entry["function"]
            for name, entry in _TOOL_REGISTRY.items()
            if saved.get(name) is not entry
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def make_host(tmp_path):
    def make():
        validator = PathValidator()
        validator.allowed_paths.add(tmp_path.resolve())
        host = _Host()
        host.path_validator = validator
        host._path_validator = validator
        host.tools = {
            "file_io": _capture(host, "register_file_io_tools"),
            "file_search": _capture(host, "register_file_search_tools"),
            "filesystem": _capture(host, "register_filesystem_tools"),
        }
        return host

    return make


@pytest.fixture
def host(make_host):
    return make_host()


@pytest.fixture
def sample(tmp_path) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def _edit(host, path, mixin="file_io"):
    return host.tools[mixin]["edit_file"](
        file_path=str(path), old_content="return 1", new_content="return 10"
    )


def _read(host, path, mixin="file_io", **kwargs):
    result = host.tools[mixin]["read_file"](file_path=str(path), **kwargs)
    if isinstance(result, dict):
        assert result["status"] == "success", result
    else:
        assert not result.startswith("Error"), result
    return result


def _bump_mtime(path: Path) -> None:
    """Move mtime a full second so no filesystem's resolution can hide it."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


# Every call that changes an existing file: (id, mixin, tool, kwargs).
GUARDED_CALLS = [
    (
        "file_io.edit_file",
        "file_io",
        "edit_file",
        {"old_content": "return 1", "new_content": "return 10"},
    ),
    (
        "file_io.edit_python_file",
        "file_io",
        "edit_python_file",
        {"old_content": "return 1", "new_content": "return 10"},
    ),
    (
        "file_io.replace_function",
        "file_io",
        "replace_function",
        {"function_name": "alpha", "new_implementation": "def alpha():\n    return 10"},
    ),
    ("file_io.write_file", "file_io", "write_file", {"content": "x = 1\n"}),
    (
        "file_io.write_python_file",
        "file_io",
        "write_python_file",
        {"content": "x = 1\n"},
    ),
    (
        "file_io.write_markdown_file",
        "file_io",
        "write_markdown_file",
        {"content": "# x\n"},
    ),
    (
        "file_search.edit_file",
        "file_search",
        "edit_file",
        {"old_content": "return 1", "new_content": "return 10"},
    ),
    ("file_search.write_file", "file_search", "write_file", {"content": "x = 1\n"}),
]


@pytest.fixture(params=GUARDED_CALLS, ids=[c[0] for c in GUARDED_CALLS])
def guarded(request, host):
    """``(path) -> result`` for one file-changing tool."""
    _, mixin, tool_name, kwargs = request.param
    function = host.tools[mixin][tool_name]

    def call(path):
        return function(file_path=str(path), **kwargs)

    call.tool_name = tool_name
    return call


class TestUnreadFileIsRefused:
    def test_refused_before_anything_is_written(self, guarded, sample):
        result = guarded(sample)

        assert result["status"] == "error"
        assert sample.read_text(encoding="utf-8") == SAMPLE

    def test_refusal_is_marked_not_executed(self, guarded, sample):
        result = guarded(sample)

        assert check_was_executed(result) is False

    def test_refusal_says_what_to_do(self, guarded, sample):
        error = guarded(sample)["error"]

        assert "read_file" in error
        assert "sample.py" in error

    def test_update_gaia_md_refuses_an_unread_gaia_md(self, host, tmp_path):
        gaia_md = tmp_path / "GAIA.md"
        gaia_md.write_text("# Mine\n", encoding="utf-8")

        result = host.tools["file_io"]["update_gaia_md"](project_root=str(tmp_path))

        assert result["status"] == "error"
        assert check_was_executed(result) is False
        assert gaia_md.read_text(encoding="utf-8") == "# Mine\n"


class TestReadingUnlocks:
    def test_every_guarded_tool_runs_after_a_full_read(self, guarded, host, sample):
        _read(host, sample)

        result = guarded(sample)

        assert result["status"] == "success", result

    @pytest.mark.parametrize(
        "mixin, kwargs",
        [
            ("file_io", {}),
            ("file_io", {"offset": 0, "limit": 10}),
            ("file_search", {}),
            ("file_search", {"offset": 5, "limit": 10}),
            ("filesystem", {}),
            ("filesystem", {"lines": 2}),
            ("filesystem", {"mode": "preview"}),
            ("filesystem", {"offset": 0, "limit": 10}),
        ],
        ids=[
            "file_io-full",
            "file_io-page",
            "file_search-full",
            "file_search-page",
            "filesystem-full",
            "filesystem-first-lines",
            "filesystem-preview",
            "filesystem-page",
        ],
    )
    def test_any_read_tool_unlocks_including_partial_reads(
        self, host, sample, mixin, kwargs
    ):
        _read(host, sample, mixin=mixin, **kwargs)

        assert _edit(host, sample)["status"] == "success"

    def test_update_gaia_md_runs_after_the_read(self, host, tmp_path):
        gaia_md = tmp_path / "GAIA.md"
        gaia_md.write_text("# Mine\n", encoding="utf-8")
        _read(host, gaia_md)

        result = host.tools["file_io"]["update_gaia_md"](project_root=str(tmp_path))

        assert result["status"] == "success", result

    def test_a_read_that_failed_unlocks_nothing(self, host, tmp_path):
        """A document format read_file refuses shows nothing, so it records nothing."""
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4 not really")
        result = host.tools["file_search"]["read_file"](file_path=str(pdf))
        assert result["status"] == "error"

        write = host.tools["file_search"]["write_file"](
            file_path=str(pdf), content="text"
        )

        assert write["status"] == "error"
        assert check_was_executed(write) is False


class TestWhatTheAgentWroteStaysUnlocked:
    @pytest.mark.parametrize("mixin", ["file_io", "file_search"])
    def test_creating_a_file_needs_no_read(self, host, tmp_path, mixin):
        new = tmp_path / "new.py"

        result = host.tools[mixin]["write_file"](file_path=str(new), content=SAMPLE)

        assert result["status"] == "success", result
        assert new.read_text(encoding="utf-8") == SAMPLE

    @pytest.mark.parametrize("mixin", ["file_io", "file_search"])
    def test_a_file_the_agent_created_can_be_edited(self, host, tmp_path, mixin):
        new = tmp_path / "new.py"
        host.tools[mixin]["write_file"](file_path=str(new), content=SAMPLE)

        assert _edit(host, new, mixin=mixin)["status"] == "success"

    @pytest.mark.parametrize("mixin", ["file_io", "file_search"])
    def test_a_file_edited_once_can_be_edited_again(self, host, sample, mixin):
        _read(host, sample, mixin=mixin)
        assert _edit(host, sample, mixin=mixin)["status"] == "success"

        again = host.tools[mixin]["edit_file"](
            file_path=str(sample), old_content="return 2", new_content="return 20"
        )

        assert again["status"] == "success", again

    def test_an_overwritten_file_can_be_edited(self, host, sample):
        _read(host, sample)
        host.tools["file_io"]["write_file"](file_path=str(sample), content=SAMPLE)

        assert _edit(host, sample)["status"] == "success"

    def test_replace_function_keeps_the_file_unlocked(self, host, sample):
        _read(host, sample)
        host.tools["file_io"]["replace_function"](
            file_path=str(sample),
            function_name="beta",
            new_implementation="def beta():\n    return 22",
        )

        assert _edit(host, sample)["status"] == "success"


class TestChangedOnDisk:
    def test_a_touched_file_is_refused(self, host, sample):
        _read(host, sample)
        _bump_mtime(sample)

        result = _edit(host, sample)

        assert result["status"] == "error"
        assert check_was_executed(result) is False
        assert "changed on disk" in result["error"]
        assert sample.read_text(encoding="utf-8") == SAMPLE

    def test_a_rewritten_file_is_refused(self, host, sample):
        _read(host, sample)
        rewritten = SAMPLE + "\n\ndef gamma():\n    return 3\n"
        sample.write_text(rewritten, encoding="utf-8")

        result = _edit(host, sample)

        assert result["status"] == "error"
        assert "changed on disk" in result["error"]
        assert sample.read_text(encoding="utf-8") == rewritten

    def test_an_overwrite_of_a_changed_file_is_refused(self, host, sample):
        _read(host, sample)
        _bump_mtime(sample)

        result = host.tools["file_io"]["write_file"](file_path=str(sample), content="")

        assert result["status"] == "error"
        assert sample.read_text(encoding="utf-8") == SAMPLE

    def test_reading_again_clears_the_refusal(self, host, sample):
        _read(host, sample)
        _bump_mtime(sample)
        assert _edit(host, sample)["status"] == "error"

        _read(host, sample)

        assert _edit(host, sample)["status"] == "success"


class TestTheRecordBelongsToOneAgent:
    def test_a_read_on_one_host_does_not_unlock_another(self, make_host, sample):
        reader, editor = make_host(), make_host()
        _read(reader, sample)

        result = _edit(editor, sample)

        assert result["status"] == "error"
        assert sample.read_text(encoding="utf-8") == SAMPLE

    def test_the_same_path_spelled_differently_is_one_file(self, host, sample):
        _read(host, sample)
        roundabout = sample.parent / ".." / sample.parent.name / sample.name

        assert _edit(host, roundabout)["status"] == "success"


# ---------------------------------------------------------------------------
# Through the agent: refused before anyone is asked to approve it
# ---------------------------------------------------------------------------


class _RecordingConsole(AgentConsole):
    """Approves every gated call and remembers which ones it was asked about."""

    def __init__(self, auto_approve_gated_tools: bool = False):
        super().__init__(auto_approve_gated_tools=auto_approve_gated_tools)
        self.asked = []

    def confirm_tool_execution(self, tool_name, tool_args):
        self.asked.append(tool_name)
        return True


class _FileAgent(Agent, FileIOToolsMixin):
    def _get_system_prompt(self) -> str:
        return "files"

    def _register_tools(self) -> None:
        self.register_file_io_tools()
        self._snapshot_tools()

    def _create_console(self):
        return _RecordingConsole()


@pytest.fixture
def agent(tmp_path):
    saved = dict(_TOOL_REGISTRY)
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            built = _FileAgent(silent_mode=True, skip_lemonade=True)
        validator = PathValidator()
        validator.allowed_paths.add(tmp_path.resolve())
        built.path_validator = validator
        built.console = _RecordingConsole()
        yield built
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _edit_args(path):
    return {
        "file_path": str(path),
        "old_content": "return 1",
        "new_content": "return 10",
    }


class TestRefusedBeforeConfirmation:
    def test_an_unread_edit_never_reaches_the_prompt(self, agent, sample):
        result = agent._execute_tool("edit_file", _edit_args(sample))

        assert result["status"] == "error"
        assert check_was_executed(result) is False
        assert agent.console.asked == []
        assert sample.read_text(encoding="utf-8") == SAMPLE

    def test_after_a_read_the_edit_is_asked_about_once_and_runs(self, agent, sample):
        agent._execute_tool("read_file", {"file_path": str(sample)})

        result = agent._execute_tool("edit_file", _edit_args(sample))

        assert result["status"] == "success", result
        assert agent.console.asked == ["edit_file"]

    def test_full_access_still_refuses_an_unread_edit(self, agent, sample):
        """The rule is about correctness, not permission."""
        agent.console = _RecordingConsole(auto_approve_gated_tools=True)

        result = agent._execute_tool("edit_file", _edit_args(sample))

        assert result["status"] == "error"
        assert check_was_executed(result) is False
        assert sample.read_text(encoding="utf-8") == SAMPLE

    def test_creating_a_file_is_asked_about_as_before(self, agent, tmp_path):
        new = tmp_path / "new.txt"

        result = agent._execute_tool(
            "write_file", {"file_path": str(new), "content": "hello"}
        )

        assert result["status"] == "success", result
        assert agent.console.asked == ["write_file"]

    def test_a_path_outside_the_sandbox_gets_the_scope_refusal(
        self, agent, tmp_path_factory
    ):
        """The read-first check must not reveal whether an out-of-scope file exists."""
        outside = tmp_path_factory.mktemp("outside") / "secret.txt"
        outside.write_text("secret", encoding="utf-8")

        result = agent._execute_tool(
            "edit_file",
            {"file_path": str(outside), "old_content": "s", "new_content": "t"},
        )

        assert result["status"] == "error"
        assert "read_file" not in result["error"]
        assert outside.read_text(encoding="utf-8") == "secret"
