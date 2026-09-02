# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the host-attribute contract (issue #3316).

Tool mixins (``RAGToolsMixin``, ``FileIOToolsMixin``) read state off the host
agent that nothing sets for them. A host that forgets used to fail in one of
two ways: a file-write tool silently skipped its confinement check and wrote
unconfined, or a bare ``self.rag`` read raised an ``AttributeError`` an outer
``except Exception`` swallowed into a misleading "temporarily unavailable"
response. This module pins the fix: writes now deny instead of proceeding
unconfined, and reads now raise a loud, actionable error naming the host
class, the missing attribute, and how to fix it — instead of masking either
failure.

These use synthetic ``Agent`` + mixin subclasses (real ``Agent``, constructed
with ``skip_lemonade=True`` to avoid touching Lemonade/the network), never a
target production agent, so the tests can't grade the implementation's own
homework.
"""

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.errors import MissingHostAttributeError
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.security import PathValidator


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save/restore the global tool registry around each test."""
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _make_agent(cls, **kwargs):
    """Construct a real Agent subclass without touching Lemonade or the network."""
    with patch("gaia.agents.base.agent.AgentSDK"):
        return cls(skip_lemonade=True, silent_mode=True, **kwargs)


def _get_tool(name):
    return _TOOL_REGISTRY[name]["function"]


class _FileIOAgentNoValidator(Agent, FileIOToolsMixin):
    """Host that registers FileIOToolsMixin's tools but never binds path_validator."""

    def _register_tools(self):
        self.register_file_io_tools()


class _FileIOAgentWithValidator(Agent, FileIOToolsMixin):
    """Reference host: binds path_validator before super().__init__()."""

    def __init__(self, **kwargs):
        self.path_validator = PathValidator()
        super().__init__(**kwargs)

    def _register_tools(self):
        self.register_file_io_tools()


class _RagAgentNoRag(Agent, RAGToolsMixin):
    """Host that registers RAGToolsMixin's tools but never binds self.rag."""

    def _register_tools(self):
        self.register_rag_tools()


class _RagAgentDisabled(Agent, RAGToolsMixin):
    """Host that explicitly disables RAG — None is a legitimate value here."""

    def __init__(self, **kwargs):
        self.rag = None
        super().__init__(**kwargs)

    def _register_tools(self):
        self.register_rag_tools()


# ---------------------------------------------------------------------------
# AC1 — missing path_validator DENIES a write; the target file must not
# exist afterward (assert on the filesystem, not just the return value).
# ---------------------------------------------------------------------------


def test_write_file_denies_when_path_validator_unbound(tmp_path):
    _make_agent(_FileIOAgentNoValidator)
    target = tmp_path / "should_not_exist.txt"

    result = _get_tool("write_file")(file_path=str(target), content="hello")

    assert result["status"] == "error"
    assert "path confinement" in result["error"]
    assert not target.exists()


@pytest.mark.parametrize(
    "tool_name,extra_kwargs",
    [
        ("write_python_file", {"content": "x = 1\n"}),
        ("write_markdown_file", {"content": "# hi"}),
        (
            "replace_function",
            {"function_name": "f", "new_implementation": "def f():\n    pass"},
        ),
    ],
)
def test_write_tools_deny_when_path_validator_unbound(
    tmp_path, tool_name, extra_kwargs
):
    _make_agent(_FileIOAgentNoValidator)
    target = tmp_path / f"{tool_name}_target.py"

    result = _get_tool(tool_name)(file_path=str(target), **extra_kwargs)

    assert result["status"] == "error"
    assert "path confinement" in result["error"]
    assert not target.exists()


def test_edit_tools_deny_when_path_validator_unbound_without_touching_file(tmp_path):
    """edit_python_file/edit_file must deny before ever reading/writing the file."""
    _make_agent(_FileIOAgentNoValidator)
    target = tmp_path / "existing.py"
    target.write_text("original\n")

    result = _get_tool("edit_python_file")(
        file_path=str(target), old_content="original", new_content="changed"
    )

    assert result["status"] == "error"
    assert "path confinement" in result["error"]
    assert target.read_text() == "original\n"  # unchanged


def test_write_file_succeeds_when_path_validator_bound(tmp_path):
    """Golden path: a properly-wired host is unaffected by the deny path."""
    agent = _make_agent(_FileIOAgentWithValidator)
    agent.path_validator.allowed_paths.add(tmp_path.resolve())
    target = tmp_path / "ok.txt"

    result = _get_tool("write_file")(file_path=str(target), content="hello")

    assert result["status"] == "success"
    assert target.read_text() == "hello"


# ---------------------------------------------------------------------------
# AC2 — missing rag raises a pinned, actionable exception (not swallowed
# into a "temporarily unavailable" fallback response).
# ---------------------------------------------------------------------------


def test_query_documents_raises_when_rag_unbound():
    _make_agent(_RagAgentNoRag)

    with pytest.raises(MissingHostAttributeError, match=r"_RagAgentNoRag.*self\.rag"):
        _get_tool("query_documents")(query="what is x?")


def test_query_specific_file_raises_when_rag_unbound():
    _make_agent(_RagAgentNoRag)

    with pytest.raises(MissingHostAttributeError, match=r"_RagAgentNoRag.*self\.rag"):
        _get_tool("query_specific_file")(file_path="doc.pdf", query="what is x?")


def test_rag_error_message_names_mixin_and_doc_anchor():
    _make_agent(_RagAgentNoRag)

    with pytest.raises(MissingHostAttributeError) as exc_info:
        _get_tool("query_documents")(query="q")

    msg = str(exc_info.value)
    assert "RAGToolsMixin" in msg
    assert "docs/spec/rag-tools-mixin.mdx#host-agent-contract" in msg


def test_rag_none_is_a_legitimate_disabled_value_not_an_error():
    """None means 'RAG intentionally disabled' — it must not raise."""
    _make_agent(_RagAgentDisabled)

    result = _get_tool("query_documents")(query="q")

    assert result["status"] == "no_documents"


# ---------------------------------------------------------------------------
# AC3 — missing path_validator at the unguarded read sites also raises
# (same contract as AC2, for the other hard-required attribute).
# ---------------------------------------------------------------------------


def test_read_file_raises_when_path_validator_unbound(tmp_path):
    _make_agent(_FileIOAgentNoValidator)

    with pytest.raises(
        MissingHostAttributeError,
        match=r"_FileIOAgentNoValidator.*self\.path_validator",
    ):
        _get_tool("read_file")(file_path=str(tmp_path / "x.py"))


def test_search_code_raises_when_path_validator_unbound(tmp_path):
    _make_agent(_FileIOAgentNoValidator)

    with pytest.raises(MissingHostAttributeError):
        _get_tool("search_code")(directory=str(tmp_path))


def test_generate_diff_raises_when_path_validator_unbound(tmp_path):
    _make_agent(_FileIOAgentNoValidator)

    with pytest.raises(MissingHostAttributeError):
        _get_tool("generate_diff")(file_path=str(tmp_path / "x.py"), new_content="x")


def test_update_gaia_md_raises_when_path_validator_unbound(tmp_path):
    _make_agent(_FileIOAgentNoValidator)

    with pytest.raises(MissingHostAttributeError):
        _get_tool("update_gaia_md")(project_root=str(tmp_path))


def test_file_io_error_message_names_mixin_and_doc_anchor(tmp_path):
    _make_agent(_FileIOAgentNoValidator)

    with pytest.raises(MissingHostAttributeError) as exc_info:
        _get_tool("read_file")(file_path=str(tmp_path / "x.py"))

    msg = str(exc_info.value)
    assert "FileIOToolsMixin" in msg
    assert "docs/spec/file-io-tools-mixin.mdx#host-agent-contract" in msg


def test_read_file_succeeds_when_path_validator_bound(tmp_path):
    """Golden path: a properly-wired host reads normally."""
    agent = _make_agent(_FileIOAgentWithValidator)
    agent.path_validator.allowed_paths.add(tmp_path.resolve())
    target = tmp_path / "readable.txt"
    target.write_text("hello world")

    result = _get_tool("read_file")(file_path=str(target))

    assert result["status"] == "success"
    assert result["content"] == "hello world"


# ---------------------------------------------------------------------------
# AC9 — no schema change: this fix alters failure behavior inside tool
# bodies, not tool metadata. Pin the registered signature/description for a
# representative tool from each touched mixin.
# ---------------------------------------------------------------------------


def test_read_file_schema_unchanged():
    _make_agent(_FileIOAgentNoValidator)

    entry = _TOOL_REGISTRY["read_file"]
    assert set(entry["parameters"]) == {"file_path"}
    assert entry["parameters"]["file_path"]["required"] is True
    assert "Read any file" in entry["description"]


def test_query_documents_schema_unchanged():
    _make_agent(_RagAgentNoRag)

    entry = _TOOL_REGISTRY["query_documents"]
    assert set(entry["parameters"]) == {"query", "debug"}
    assert entry["parameters"]["query"]["required"] is True
    assert entry["parameters"]["debug"]["required"] is False
    assert "Query indexed documents" in entry["description"]
