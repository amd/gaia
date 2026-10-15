# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GAIA_ALLOWED_PATHS`` narrows every agent's file scope, and never widens it.

An evaluation run confines the agent to its task folder so ordinary file tools
cannot read the graders' answer keys stored elsewhere under the user's home.
Each test plants an answer key outside the narrowed scope and reaches for it.
"""

import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin
from gaia.agents.tools.filesystem_tools import FileSystemToolsMixin
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.agents.tools.skill_library_tools import SkillLibraryToolsMixin
from gaia.security import (
    ALLOWED_PATHS_ENV,
    AllowedPathsEnvError,
    PathValidator,
    env_allowed_paths,
    narrow_allowed_paths,
)

IS_WINDOWS = sys.platform == "win32"


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def _no_inherited_env(monkeypatch):
    monkeypatch.delenv(ALLOWED_PATHS_ENV, raising=False)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A home folder holding the task, a look-alike sibling, and answer keys."""
    home = tmp_path / "home"
    task = home / "work" / "task"
    task.mkdir(parents=True)
    (task / "notes.txt").write_text("task data\n", encoding="utf-8")
    sibling = home / "work" / "task-other"
    sibling.mkdir()
    (sibling / "answer_key.txt").write_text("answer\n", encoding="utf-8")
    graders = home / "graders"
    graders.mkdir()
    (graders / "answer_key.txt").write_text("answer\n", encoding="utf-8")
    docs = home / "Documents"
    docs.mkdir()
    (docs / "answer_key.txt").write_text("answer\n", encoding="utf-8")
    beside_home = tmp_path / "beside_home"
    beside_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return SimpleNamespace(
        home=home.resolve(),
        task=task.resolve(),
        sibling=sibling.resolve(),
        key=(graders / "answer_key.txt").resolve(),
        beside_home=beside_home.resolve(),
    )


@pytest.fixture
def confined(tree, monkeypatch):
    monkeypatch.setenv(ALLOWED_PATHS_ENV, str(tree.task))
    return tree


@pytest.fixture
def validator(confined):
    """The flagship's default scope (home), narrowed to the task folder."""
    v = PathValidator(allowed_paths=[str(confined.home)])
    v.set_access_prompt(lambda _path: True)  # an approve-everything harness
    return v


@pytest.fixture
def scratch():
    path = Path(tempfile.mkdtemp(prefix="gaia-scratch-envtest-")).resolve()
    yield path
    shutil.rmtree(path)


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


def _text(result):
    return result if isinstance(result, str) else json.dumps(result)


# ── Parsing the variable ────────────────────────────────────────────────────


class TestUnsetChangesNothing:
    def test_no_variable_no_ceiling(self):
        assert env_allowed_paths() is None

    def test_configured_scope_is_returned_as_is(self, tree):
        paths = [str(tree.home)]
        assert narrow_allowed_paths(paths) is paths
        assert narrow_allowed_paths(None) is None

    def test_validator_keeps_its_default_scope(self, tree):
        v = PathValidator(allowed_paths=[str(tree.home)])
        assert v.allowed_paths == {tree.home}
        assert v.is_path_allowed(str(tree.key), prompt_user=False)
        assert v.in_env_scope(tree.key)


class TestInvalidValuesFailLoudly:
    @pytest.mark.parametrize(
        "value, problem",
        [
            ("", "entry 1 is empty"),
            ("   ", "entry 1 is empty"),
            ("relative/folder", "is not an absolute path"),
            ("~/work", "is not an absolute path"),
        ],
    )
    def test_unusable_value_raises(self, monkeypatch, value, problem):
        monkeypatch.setenv(ALLOWED_PATHS_ENV, value)

        with pytest.raises(AllowedPathsEnvError, match=problem) as info:
            env_allowed_paths()
        assert "unset it" in str(info.value)
        assert "amd-gaia.ai/docs/" in str(info.value)

    def test_empty_entry_between_good_ones_raises(self, tree, monkeypatch):
        monkeypatch.setenv(
            ALLOWED_PATHS_ENV, f"{tree.task}{os.pathsep}{os.pathsep}{tree.home}"
        )
        with pytest.raises(AllowedPathsEnvError, match="entry 2 is empty"):
            env_allowed_paths()

    def test_missing_folder_raises(self, tree, monkeypatch):
        monkeypatch.setenv(ALLOWED_PATHS_ENV, str(tree.home / "no-such-folder"))
        with pytest.raises(AllowedPathsEnvError, match="does not exist"):
            env_allowed_paths()

    def test_validator_construction_fails_instead_of_falling_back(
        self, tree, monkeypatch
    ):
        monkeypatch.setenv(ALLOWED_PATHS_ENV, "relative")
        with pytest.raises(AllowedPathsEnvError):
            PathValidator(allowed_paths=[str(tree.home)])

    def test_it_is_a_value_error(self):
        assert issubclass(AllowedPathsEnvError, ValueError)


# ── Narrowing ───────────────────────────────────────────────────────────────


class TestNarrowing:
    def test_scope_becomes_the_task_folder(self, validator, confined):
        assert validator.allowed_paths == {confined.task}

    def test_task_file_is_readable(self, validator, confined):
        allowed, reason = validator.validate_read(
            str(confined.task / "notes.txt"), prompt_user=False
        )
        assert allowed, reason

    def test_answer_key_is_refused_without_asking(self, confined):
        asked = []
        v = PathValidator(allowed_paths=[str(confined.home)])
        v.set_access_prompt(lambda path: asked.append(path) or True)

        allowed, reason = v.validate_read(str(confined.key))

        assert not allowed
        assert ALLOWED_PATHS_ENV in reason
        assert asked == []

    def test_narrow_allowed_paths_intersects(self, confined):
        assert narrow_allowed_paths([str(confined.home)]) == [str(confined.task)]

    def test_none_means_the_working_directory(self, confined, monkeypatch):
        monkeypatch.chdir(confined.home)
        assert narrow_allowed_paths(None) == [str(confined.task)]

    def test_a_narrower_configured_scope_stays_narrow(self, confined):
        inner = confined.task / "inner"
        inner.mkdir()
        v = PathValidator(allowed_paths=[str(inner)])
        assert v.allowed_paths == {inner}
        assert not v.is_path_allowed(
            str(confined.task / "notes.txt"), prompt_user=False
        )

    def test_attached_file_outside_is_dropped(self, validator, confined):
        validator.add_allowed_path(str(confined.key))
        assert validator.allowed_paths == {confined.task}
        assert not validator.is_path_allowed(str(confined.key))

    def test_attached_file_inside_is_kept(self, validator, confined):
        notes = confined.task / "notes.txt"
        validator.add_allowed_path(str(notes))
        assert notes in validator.allowed_paths

    def test_a_grant_added_behind_the_api_is_still_held(self, validator, confined):
        validator.allowed_paths.add(confined.home)
        assert not validator.is_path_allowed(str(confined.key), prompt_user=False)


class TestEntriesCannotWiden:
    def test_drive_root_and_home_do_not_widen(self, tree, monkeypatch):
        anchor = Path(tree.home.anchor)
        monkeypatch.setenv(
            ALLOWED_PATHS_ENV, os.pathsep.join([str(anchor), str(Path.home())])
        )
        v = PathValidator(allowed_paths=[str(tree.task)])

        assert v.allowed_paths == {tree.task}
        assert not v.is_path_allowed(str(tree.key), prompt_user=False)
        assert not v.is_path_allowed(str(tree.beside_home), prompt_user=False)

    def test_entry_outside_the_default_scope_is_ignored(
        self, tree, monkeypatch, caplog
    ):
        monkeypatch.setenv(
            ALLOWED_PATHS_ENV, os.pathsep.join([str(tree.task), str(tree.beside_home)])
        )
        with caplog.at_level(logging.WARNING, logger="gaia.security"):
            v = PathValidator(allowed_paths=[str(tree.home)])

        assert v.allowed_paths == {tree.task}
        assert not v.is_path_allowed(str(tree.beside_home), prompt_user=False)
        assert any(str(tree.beside_home) in r.getMessage() for r in caplog.records)

    def test_only_outside_entries_leave_nothing(self, tree, monkeypatch):
        monkeypatch.setenv(ALLOWED_PATHS_ENV, str(tree.beside_home))
        v = PathValidator(allowed_paths=[str(tree.home)])

        assert v.allowed_paths == set()
        assert not v.is_path_allowed(str(tree.task / "notes.txt"), prompt_user=False)
        assert not v.is_path_allowed(str(tree.beside_home), prompt_user=False)


class TestScratchDirStaysWritable:
    def test_scratch_outside_the_scope_is_writable(self, validator, scratch):
        validator.set_scratch_dir(str(scratch))

        allowed, reason = validator.validate_write(
            str(scratch / "helper.py"), content_size=10, prompt_user=False
        )
        assert allowed, reason
        assert validator.in_env_scope(scratch / "helper.py")

    def test_scratch_does_not_open_its_parent(self, validator, scratch):
        validator.set_scratch_dir(str(scratch))
        assert not validator.is_path_allowed(
            str(scratch.parent / "elsewhere.txt"), prompt_user=False
        )


# ── Escapes ─────────────────────────────────────────────────────────────────


class TestEscapes:
    def test_dotdot_out_of_the_task(self, validator, confined):
        sneaky = confined.task / ".." / ".." / "graders" / "answer_key.txt"
        assert not validator.is_path_allowed(str(sneaky))

    def test_prefix_lookalike_sibling(self, validator, confined):
        assert not validator.is_path_allowed(str(confined.sibling / "answer_key.txt"))
        assert not validator.is_path_allowed(str(confined.sibling))

    def test_symlink_inside_the_task_pointing_out(self, validator, confined):
        link = confined.task / "link"
        try:
            link.symlink_to(confined.key.parent, target_is_directory=True)
        except OSError:
            pytest.skip("symlinks need privilege on this machine")
        assert not validator.is_path_allowed(str(link / "answer_key.txt"))

    @pytest.mark.skipif(not IS_WINDOWS, reason="junctions are Windows-only")
    def test_junction_inside_the_task_pointing_out(self, validator, confined):
        import _winapi

        link = confined.task / "junction"
        _winapi.CreateJunction(str(confined.key.parent), str(link))
        assert (link / "answer_key.txt").exists()
        assert not validator.is_path_allowed(str(link / "answer_key.txt"))

    @pytest.mark.skipif(not IS_WINDOWS, reason="Windows path forms")
    def test_case_variations(self, validator, confined):
        assert not validator.is_path_allowed(str(confined.key).upper())
        assert not validator.is_path_allowed(str(confined.sibling).swapcase())
        assert validator.is_path_allowed(
            str(confined.task / "notes.txt").upper(), prompt_user=False
        )

    @pytest.mark.skipif(not IS_WINDOWS, reason="Windows path forms")
    def test_unc_and_device_paths(self, validator, confined):
        drive = confined.key.drive.rstrip(":")
        tail = str(confined.key)[len(confined.key.drive) :]
        forms = [
            "\\\\?\\" + str(confined.key),
            f"\\\\localhost\\{drive}$" + tail,
            f"\\\\127.0.0.1\\{drive}$" + tail,
        ]
        for form in forms:
            assert not validator.is_path_allowed(form), form

    @pytest.mark.skipif(not IS_WINDOWS, reason="8.3 short names are Windows-only")
    def test_short_name_of_the_lookalike_sibling(self, validator, confined):
        import ctypes

        target = confined.sibling.parent / "task-other-longname-folder"
        target.mkdir()
        (target / "answer_key.txt").write_text("answer\n", encoding="utf-8")
        buffer = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(str(target), buffer, 1024)
        short = buffer.value
        if not short or Path(short).name == target.name:
            pytest.skip("8.3 name generation is disabled on this volume")
        assert not validator.is_path_allowed(short + "\\answer_key.txt")

    @pytest.mark.skipif(not IS_WINDOWS, reason="8.3 short names are Windows-only")
    def test_short_name_env_entry_still_matches(self, tree, monkeypatch):
        import ctypes

        buffer = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(str(tree.task), buffer, 1024)
        monkeypatch.setenv(ALLOWED_PATHS_ENV, buffer.value or str(tree.task))
        v = PathValidator(allowed_paths=[str(tree.home)])
        assert v.is_path_allowed(str(tree.task / "notes.txt"), prompt_user=False)
        assert not v.is_path_allowed(str(tree.key), prompt_user=False)


# ── The tools ───────────────────────────────────────────────────────────────


class _Host(FileIOToolsMixin, FileSystemToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator
        self._bookmarks = {}


class TestFileTools:
    @pytest.fixture
    def tools(self, validator):
        _Host(validator).register_file_io_tools()
        return validator

    def test_read_file_refuses_the_answer_key(self, tools, confined):
        result = _tool("read_file")(file_path=str(confined.key))
        assert result["status"] == "error"
        assert "answer" not in result.get("content", "")
        assert ALLOWED_PATHS_ENV in _text(result)

    def test_read_file_reads_the_task(self, tools, confined):
        result = _tool("read_file")(file_path=str(confined.task / "notes.txt"))
        assert result["status"] == "success", result

    def test_write_file_refuses_outside(self, tools, confined):
        target = confined.home / "planted.txt"
        result = _tool("write_file")(file_path=str(target), content="x")
        assert result["status"] == "error"
        assert not target.exists()


class TestListingAndSearch:
    @pytest.fixture
    def tools(self, validator):
        _Host(validator).register_filesystem_tools()
        return validator

    def test_browse_outside_is_refused(self, tools, confined):
        result = _tool("browse_directory")(path=str(confined.key.parent))
        assert "answer_key" not in result
        assert ALLOWED_PATHS_ENV in result

    def test_tree_outside_is_refused(self, tools, confined):
        result = _tool("tree")(path=str(confined.home))
        assert "answer_key" not in result

    @pytest.mark.parametrize("scope", ["home", "smart"])
    def test_wide_name_search_stays_in_the_task(self, tools, confined, scope):
        result = _tool("find_files")(
            query="answer_key", search_type="name", scope=scope
        )
        assert "answer_key" not in result.replace("'answer_key'", "")

    def test_content_search_stays_in_the_task(self, tools, confined):
        result = _tool("find_files")(
            query="answer", search_type="content", scope="home"
        )
        assert "answer_key" not in result


class _SearchHost(FileSearchToolsMixin):
    def __init__(self, validator):
        self.path_validator = validator


class TestSearchFile:
    def test_common_folders_are_not_searched_outside_the_task(
        self, validator, confined
    ):
        _SearchHost(validator).register_file_search_tools()

        result = _tool("search_file")(file_pattern="answer_key")

        assert "answer_key.txt" not in json.dumps(result.get("files", []))


class TestIndexDocument:
    def test_index_document_refuses_the_answer_key(self, validator, confined):
        host = RAGToolsMixin()
        host.path_validator = validator
        host.rag = MagicMock()
        host.rag.indexed_files = set()
        host.indexed_files = set()
        host.current_session = None
        host.rebuild_system_prompt = MagicMock()
        host.register_rag_tools()

        result = _tool("index_document")(file_path=str(confined.key))

        assert result["status"] == "error"
        host.rag.index_document.assert_not_called()


class TestShell:
    @pytest.fixture
    def run(self, validator):
        host = ShellToolsMixin()
        host.path_validator = validator
        host.register_shell_tools()
        return _tool("run_shell_command")

    def test_reading_the_key_by_argument_is_refused(self, run, confined):
        result = run(
            command=f'cat "{confined.key}"', working_directory=str(confined.task)
        )
        assert result["status"] == "error"
        assert "answer" not in result.get("stdout", "")

    def test_working_directory_outside_is_refused(self, run, confined):
        result = run(command="ls", working_directory=str(confined.key.parent))
        assert result["status"] == "error"


class TestSkillCapture:
    def test_local_folder_outside_is_not_captured(self, validator, confined):
        host = SkillLibraryToolsMixin()
        host.path_validator = validator
        host.skill_manager = MagicMock()
        host.register_skill_library_tools()

        result = _tool("capture_skill")(source=str(confined.key.parent))

        assert result["status"] == "error"
        assert ALLOWED_PATHS_ENV in result["error"]


class TestChatAgentInheritsTheNarrowedScope:
    """One narrowing in ChatAgent reaches every consumer of its scope."""

    def test_validator_rag_and_config_agree(self, confined):
        pytest.importorskip("gaia_agent_chat")
        from unittest.mock import patch

        from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

        config = ChatAgentConfig(
            prompt_profile="doc",
            silent_mode=True,
            allowed_paths=[str(confined.home)],
        )
        with (
            patch("gaia_agent_chat.agent.RAGSDK"),
            patch("gaia_agent_chat.agent.SessionManager"),
        ):
            agent = ChatAgent(config)

        assert agent.config.allowed_paths == [str(confined.task)]
        assert agent.allowed_paths == [confined.task]
        assert agent.path_validator.allowed_paths <= {
            confined.task,
            agent.path_validator.scratch_dir,
        }
        assert agent._rag_config.allowed_paths == [str(confined.task)]
        assert config.allowed_paths == [str(confined.home)]


class TestHostsRefuseToStart:
    def test_agent_ui_server(self, monkeypatch):
        pytest.importorskip("fastapi")
        from gaia.ui.server import create_app

        monkeypatch.setenv(ALLOWED_PATHS_ENV, "relative")
        with pytest.raises(AllowedPathsEnvError):
            create_app(db_path=":memory:")

    def test_flagship_sidecar(self, monkeypatch, capsys):
        pytest.importorskip("gaia_agent")
        from gaia_agent.server import serve_http

        monkeypatch.setenv(ALLOWED_PATHS_ENV, "relative")
        assert serve_http(["--serve"]) == 2
        assert ALLOWED_PATHS_ENV in capsys.readouterr().err


class TestProjectMap:
    def test_a_project_outside_the_scope_is_not_mapped(self, validator, confined):
        from gaia.agents.base.project_map import ProjectMapMixin

        host = ProjectMapMixin()
        host.path_validator = validator
        host.config = SimpleNamespace(project_root=str(confined.key.parent))

        assert host._project_map_root() is None

    def test_the_task_folder_is_mapped(self, validator, confined):
        from gaia.agents.base.project_map import ProjectMapMixin

        host = ProjectMapMixin()
        host.path_validator = validator
        host.config = SimpleNamespace(project_root=str(confined.task))

        assert host._project_map_root() is not None


class TestNothingLeftInScope:
    """When every entry falls outside the scope, code search reaches nothing."""

    def test_empty_ceiling_is_kept_empty(self, tmp_path):
        from gaia.agents.tools.code_index_tools import CodeIndexToolsMixin

        host = CodeIndexToolsMixin()
        host._init_code_index_state(repo_path=str(tmp_path), ceiling_paths=[])
        assert host._code_index_ceilings == ()

    def test_flagship_code_search_start_survives_an_empty_scope(self):
        pytest.importorskip("gaia_agent")
        from gaia_agent.agent import _code_search_start

        assert _code_search_start([]) == os.getcwd()


class TestRootEntries:
    def test_a_lone_drive_root_keeps_the_default_scope(self, tree, monkeypatch):
        monkeypatch.setenv(ALLOWED_PATHS_ENV, tree.home.anchor)
        v = PathValidator(allowed_paths=[str(tree.task)])

        assert v.allowed_paths == {tree.task}
        assert not v.is_path_allowed(str(tree.key), prompt_user=False)


class TestShellVariables:
    @pytest.mark.parametrize("operand", ["%USERPROFILE%", "$HOME", "~"])
    def test_a_bare_home_variable_is_checked(self, validator, confined, operand):
        host = ShellToolsMixin()
        host.path_validator = validator
        host.register_shell_tools()

        result = _tool("run_shell_command")(
            command=f"ls {operand}", working_directory=str(confined.task)
        )

        assert result["status"] == "error"
        assert "answer_key" not in _text(result)


class TestEmptyScope:
    """The allowlist sits outside the default scope, so nothing is left."""

    @pytest.fixture
    def empty(self, tree, monkeypatch):
        monkeypatch.setenv(ALLOWED_PATHS_ENV, str(tree.beside_home))
        monkeypatch.chdir(tree.home)
        v = PathValidator(allowed_paths=[str(tree.home)])
        assert v.allowed_paths == set()
        return v

    def test_search_roots_are_empty_not_the_cwd(self, empty):
        from gaia.agents.tools.search_scope import search_roots

        assert search_roots(SimpleNamespace(path_validator=empty)) == []

    def test_search_file_finds_nothing(self, empty):
        _SearchHost(empty).register_file_search_tools()

        result = _tool("search_file")(file_pattern="answer_key")

        assert "answer_key.txt" not in json.dumps(result.get("files", []))

    def test_search_directory_finds_nothing(self, empty):
        _SearchHost(empty).register_file_search_tools()

        result = _tool("search_directory")(directory_name="graders")

        assert "graders" not in json.dumps(result.get("directories", []))

    def test_code_search_refuses_the_working_directory(self, empty, tree):
        from gaia.agents.tools import code_index_tools
        from gaia.agents.tools.code_index_tools import CodeIndexToolsMixin

        if not code_index_tools._CODE_INDEX_AVAILABLE:
            pytest.skip("code index dependencies are not installed")
        host = CodeIndexToolsMixin()
        host.path_validator = empty
        host._init_code_index_state(repo_path=str(tree.home / "work"), ceiling_paths=[])
        host._get_code_index_sdk = MagicMock()
        host.register_code_index_tools()

        for name, kwargs in (
            ("search_code_index", {"query": "answer"}),
            ("index_codebase", {}),
        ):
            result = json.loads(_tool(name)(**kwargs))
            assert "error" in result, name
        host._get_code_index_sdk.assert_not_called()


class TestDeepSearch:
    def test_deep_search_walks_only_the_scope(self, validator, confined):
        _SearchHost(validator).register_file_search_tools()

        result = _tool("search_file")(file_pattern="answer_key", deep_search=True)

        assert "answer_key.txt" not in json.dumps(result.get("files", []))
