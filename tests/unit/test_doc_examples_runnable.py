# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Executes the guide's own example code blocks (not copies) from
docs/guides/custom-agent.mdx to catch runtime-only defects that
util/check_doc_code.py's syntax/import checks cannot see.
"""

import importlib
import inspect
import sys
import textwrap
from pathlib import Path

import pytest

_util_dir = str(Path(__file__).resolve().parents[2] / "util")
if _util_dir not in sys.path:
    sys.path.insert(0, _util_dir)

check_doc_code = importlib.import_module("check_doc_code")

from gaia.agents.base import tools as tools_module  # noqa: E402
from gaia.security import PathValidator  # noqa: E402

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "guides" / "custom-agent.mdx"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _get_example_block(marker: str):
    blocks = check_doc_code.extract_code_blocks(DOC_PATH, REPO_ROOT)
    found = [b for b in blocks if marker in b.source]
    assert len(found) == 1, (
        f"Expected exactly one code block containing {marker!r} in "
        f"{DOC_PATH}, found {len(found)}. The custom-agent guide's "
        "Examples section may have been renamed or removed."
    )
    return found[0]


def _exec_block(block) -> dict:
    """Dedent and exec a doc code block into a fresh namespace, returning it."""
    dedented = textwrap.dedent(block.source)
    compiled = compile(dedented, f"<doc:{DOC_PATH.name}:{block.line}>", "exec")
    namespace: dict = {}
    exec(compiled, namespace)  # pylint: disable=exec-used
    return namespace


def test_research_agent_example_is_runnable(monkeypatch):
    """AC1/AC2: the ResearchAgent example must actually construct and query."""
    block = _get_example_block("class ResearchAgent(Agent")

    # Isolate the module-level tool registry so this example's
    # _register_tools().clear() can't corrupt other tests.
    monkeypatch.setattr(
        tools_module, "_TOOL_REGISTRY", dict(tools_module._TOOL_REGISTRY)
    )

    namespace = _exec_block(block)
    ResearchAgent = namespace["ResearchAgent"]

    agent = ResearchAgent()

    # AC1: real, functioning attributes wired up by RAGToolsMixin/FileSearchToolsMixin.
    assert agent.rag is not None
    assert isinstance(agent.indexed_files, (set, list))
    assert isinstance(agent.max_chunks, int)
    assert hasattr(agent, "current_session")
    assert hasattr(agent, "session_manager")

    # AC2: the registered query_documents tool must be callable and behave
    # honestly when no documents have been indexed.
    query_entry = tools_module._TOOL_REGISTRY.get("query_documents")
    assert query_entry is not None, "query_documents tool was not registered"
    query_documents = query_entry["function"]

    # Tool functions are closures over `self` (bound at registration time),
    # not unbound methods — call with the tool's own declared arguments only.
    result = query_documents("test query")

    assert "error" not in result
    assert result["status"] == "no_documents"


def test_code_review_agent_example_is_runnable(monkeypatch, tmp_path):
    """AC3/AC4: the CodeReviewAgent example must actually construct and read a file."""
    block = _get_example_block("class CodeReviewAgent(Agent")

    # Isolate the module-level tool registry from the ResearchAgent test above
    # (and from any other test running _register_tools().clear()).
    monkeypatch.setattr(
        tools_module, "_TOOL_REGISTRY", dict(tools_module._TOOL_REGISTRY)
    )

    namespace = _exec_block(block)
    CodeReviewAgent = namespace["CodeReviewAgent"]

    # Confine the agent's allowed paths to tmp_path rather than the CWD
    # default. Introspect the constructor instead of assuming a specific
    # parameter name, since that's exactly what the doc fix may change. If
    # the (currently broken) example exposes no such parameter at all,
    # fall back to a bare construction so the real defect (path_validator
    # never getting created/confined) still surfaces below.
    sig = inspect.signature(CodeReviewAgent.__init__)
    path_params = [
        name
        for name in sig.parameters
        if name in ("documents", "allowed_paths", "allowed_dirs", "paths")
    ]
    if path_params:
        agent = CodeReviewAgent(**{path_params[0]: [str(tmp_path)]})
    else:
        agent = CodeReviewAgent()

    # AC3: path_validator must be a real PathValidator confined to tmp_path,
    # not the CWD-default fallback.
    assert isinstance(agent.path_validator, PathValidator)

    # AC4: reading a real file within the allowed root must succeed.
    sample_file = tmp_path / "sample.py"
    sample_file.write_text("print('hello')\n", encoding="utf-8")

    read_entry = tools_module._TOOL_REGISTRY.get("read_file")
    assert read_entry is not None, "read_file tool was not registered"
    read_file = read_entry["function"]

    # Tool functions are closures over `self` (bound at registration time),
    # not unbound methods — call with the tool's own declared arguments only.
    result = read_file(str(sample_file))

    assert result["status"] == "success"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
