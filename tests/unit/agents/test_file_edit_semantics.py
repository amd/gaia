# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for the shared match-and-replace semantics of GAIA's file-editing tools.

Purpose: an ``old_content`` that matches more than one place in a file used to
be replaced at the *first* match and reported as a success, so the agent
believed it had changed one region while the diff touched another (#3377).
These tests pin the replacement contract for every ``edit_*`` tool:

- exactly one match replaces; zero or several is an error and writes nothing
- a rejected edit carries the file's current content, so the retry needs no
  extra read
- an edit against a file that changed since it was read is rejected

The behaviour table is parametrized over all three implementations, so the
suite fails if any one of them drifts from the others.

No LLM or external service required.
"""

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_edit import FileStateTracker, apply_unique_replacement

# Valid Python (edit_python_file rejects edits that break the parse) with a
# body line that deliberately appears twice.
SAMPLE = '''"""Module."""


def alpha():
    value = 1
    return value


def beta():
    value = 1
    return value
'''

DUPLICATED = "    value = 1"
UNIQUE_OLD = "def alpha():\n    value = 1"
UNIQUE_NEW = "def alpha():\n    value = 99"

# (id, module, mixin class, registrar, tool name)
EDIT_TOOLS = [
    (
        "file_io_tools.edit_file",
        "gaia.agents.tools.file_io_tools",
        "FileIOToolsMixin",
        "register_file_io_tools",
        "edit_file",
    ),
    (
        "file_io_tools.edit_python_file",
        "gaia.agents.tools.file_io_tools",
        "FileIOToolsMixin",
        "register_file_io_tools",
        "edit_python_file",
    ),
    (
        "file_tools.edit_file",
        "gaia.agents.tools.file_tools",
        "FileSearchToolsMixin",
        "register_file_search_tools",
        "edit_file",
    ),
]

EDIT_TOOL_IDS = [entry[0] for entry in EDIT_TOOLS]


@pytest.fixture(autouse=True)
def clean_tracker():
    """The tracker is process-wide; no test may inherit another's ledger."""
    FileStateTracker.instance().clear()
    yield
    FileStateTracker.instance().clear()


@pytest.fixture(params=EDIT_TOOLS, ids=EDIT_TOOL_IDS)
def edit_tool(request):
    """Every edit tool, behind one ``(path, old, new) -> dict`` signature.

    The two ``edit_file`` implementations register under the same name and
    overwrite each other in the registry, so each is registered and captured
    on its own.
    """
    _, module_name, class_name, registrar, tool_name = request.param
    module = importlib.import_module(module_name)
    mixin = getattr(module, class_name)()

    saved = dict(_TOOL_REGISTRY)
    try:
        getattr(mixin, registrar)()
        entry = _TOOL_REGISTRY.get(tool_name)
        assert entry is not None, f"{tool_name} was not registered by {registrar}"
        function = entry["function"]

        def call(path, old, new):
            return function(str(path), old, new)

        call.module_name = module_name
        call.tool_name = tool_name
        yield call
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def sample_file(tmp_path) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


# ============================================================================
# 1. AMBIGUITY IS AN ERROR, NOT A FIRST-MATCH REPLACEMENT
# ============================================================================


class TestAmbiguousOldContent:
    """A non-unique old_content must be refused, with the count named."""

    def test_ambiguous_edit_is_rejected(self, edit_tool, sample_file):
        result = edit_tool(sample_file, DUPLICATED, "    value = 2")
        assert result["status"] == "error"

    def test_ambiguous_edit_writes_nothing(self, edit_tool, sample_file):
        edit_tool(sample_file, DUPLICATED, "    value = 2")
        assert sample_file.read_text(encoding="utf-8") == SAMPLE

    def test_ambiguous_error_states_the_match_count(self, edit_tool, sample_file):
        result = edit_tool(sample_file, DUPLICATED, "    value = 2")
        assert result["match_count"] == 2
        assert "2" in result["error"]

    def test_ambiguous_error_locates_every_match(self, edit_tool, sample_file):
        result = edit_tool(sample_file, DUPLICATED, "    value = 2")
        # SAMPLE puts the duplicated line on lines 5 and 10.
        assert result["match_lines"] == [5, 10]
        assert [m["line"] for m in result["matches"]] == [5, 10]

    def test_ambiguous_error_says_how_to_retry(self, edit_tool, sample_file):
        result = edit_tool(sample_file, DUPLICATED, "    value = 2")
        assert "surrounding lines" in result["error"]


# ============================================================================
# 2. A REJECTED EDIT CARRIES CURRENT CONTENT
# ============================================================================


class TestNotFoundCarriesContent:
    """The retry must not need a separate re-read to succeed."""

    def test_not_found_is_an_error_with_zero_matches(self, edit_tool, sample_file):
        result = edit_tool(sample_file, "def gamma():", "def delta():")
        assert result["status"] == "error"
        assert result["match_count"] == 0

    def test_not_found_error_carries_file_content(self, edit_tool, sample_file):
        result = edit_tool(sample_file, "def gamma():", "def delta():")
        excerpt = result["current_content"]
        assert excerpt, "not-found error returned no current content"
        assert excerpt in SAMPLE, "excerpt is not verbatim from the file"

    def test_not_found_error_gives_the_excerpt_line_range(self, edit_tool, sample_file):
        result = edit_tool(sample_file, "def gamma():", "def delta():")
        assert result["current_content_start_line"] >= 1
        assert (
            result["current_content_end_line"] <= result["current_content_total_lines"]
        )

    def test_whitespace_only_mismatch_is_called_out(self, edit_tool, sample_file):
        """The commonest not-found cause deserves naming, not guessing."""
        result = edit_tool(sample_file, "def  alpha():", "def gamma():")
        assert "whitespace-insensitive" in result["error"]

    def test_empty_old_content_is_rejected(self, edit_tool, sample_file):
        result = edit_tool(sample_file, "", "anything")
        assert result["status"] == "error"
        assert sample_file.read_text(encoding="utf-8") == SAMPLE


# ============================================================================
# 3. STALENESS
# ============================================================================


class TestStalenessRejection:
    """An edit against a file that moved under the agent must not clobber it."""

    def test_stale_edit_is_rejected(self, edit_tool, sample_file):
        FileStateTracker.instance().record_read(str(sample_file), "something older")
        result = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert result["status"] == "error"
        assert result["stale"] is True

    def test_stale_edit_writes_nothing(self, edit_tool, sample_file):
        FileStateTracker.instance().record_read(str(sample_file), "something older")
        edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert sample_file.read_text(encoding="utf-8") == SAMPLE

    def test_stale_rejection_carries_current_content(self, edit_tool, sample_file):
        FileStateTracker.instance().record_read(str(sample_file), "something older")
        result = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert result["current_content"] in SAMPLE

    def test_stale_rejection_names_both_hashes(self, edit_tool, sample_file):
        FileStateTracker.instance().record_read(str(sample_file), "something older")
        result = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert result["hash_at_read"] != result["hash_now"]
        assert result["hash_at_read"] in result["error"]

    def test_matching_read_does_not_block_the_edit(self, edit_tool, sample_file):
        FileStateTracker.instance().record_read(str(sample_file), SAMPLE)
        result = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert result["status"] == "success"

    def test_unread_file_is_not_blocked(self, edit_tool, sample_file):
        """No record means nothing stale to be wrong about."""
        assert not FileStateTracker.instance().has_record(str(sample_file))
        result = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert result["status"] == "success"

    def test_consecutive_edits_need_no_intervening_read(self, edit_tool, sample_file):
        """A successful edit re-anchors the ledger to what it just wrote."""
        first = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert first["status"] == "success"
        second = edit_tool(sample_file, "def beta():", "def gamma():")
        assert second["status"] == "success"

    def test_stale_rejection_is_not_a_dead_end(self, edit_tool, sample_file):
        """Rejecting forever is as broken as clobbering.

        The rejection hands the current content back, so it counts as a read:
        the corrected retry must go through instead of hitting the superseded
        hash again.
        """
        FileStateTracker.instance().record_read(str(sample_file), "something older")
        rejected = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert rejected["status"] == "error"

        retry = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert retry["status"] == "success", "stale rejection livelocked the agent"


# ============================================================================
# 4. THE HAPPY PATH STILL WORKS
# ============================================================================


class TestUniqueReplacement:
    def test_unique_old_content_is_replaced(self, edit_tool, sample_file):
        result = edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert result["status"] == "success"
        assert sample_file.read_text(encoding="utf-8") == SAMPLE.replace(
            UNIQUE_OLD, UNIQUE_NEW
        )

    def test_only_the_matched_region_changes(self, edit_tool, sample_file):
        edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        updated = sample_file.read_text(encoding="utf-8")
        assert "def alpha():\n    value = 99" in updated
        assert "def beta():\n    value = 1" in updated


# ============================================================================
# 5. THE IMPLEMENTATIONS CANNOT DIVERGE
# ============================================================================


class TestImplementationsCannotDiverge:
    """Every edit site must decide *what* to replace in exactly one place.

    The behaviour classes above already run against all three tools; these
    tests stop a future edit from quietly reintroducing a private
    match-and-replace that passes those by accident.
    """

    def test_every_edit_tool_delegates_to_the_shared_helper(
        self, edit_tool, sample_file
    ):
        target = f"{edit_tool.module_name}.apply_unique_replacement"
        with patch(target, wraps=apply_unique_replacement) as spy:
            edit_tool(sample_file, UNIQUE_OLD, UNIQUE_NEW)
        assert spy.call_count == 1, (
            f"{edit_tool.tool_name} did not route its replacement through "
            "apply_unique_replacement"
        )

    @pytest.mark.parametrize(
        "module_file",
        [
            "src/gaia/agents/tools/file_io_tools.py",
            "src/gaia/agents/tools/file_tools.py",
        ],
    )
    def test_no_edit_site_keeps_a_first_match_replace(self, module_file):
        """``.replace(old_content, ..., 1)`` is the bug; it must not come back."""
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / module_file).read_text(encoding="utf-8")
        assert (
            "replace(old_content" not in source
        ), f"{module_file} still does its own old_content replacement"

    def test_the_three_tools_agree_on_the_error_shape(self, tmp_path):
        """Same input, same keys — a caller can handle one shape, not three."""
        shapes = {}
        for tool_id, module_name, class_name, registrar, tool_name in EDIT_TOOLS:
            module = importlib.import_module(module_name)
            mixin = getattr(module, class_name)()
            saved = dict(_TOOL_REGISTRY)
            try:
                getattr(mixin, registrar)()
                function = _TOOL_REGISTRY[tool_name]["function"]
                path = tmp_path / f"{tool_id.replace('.', '_')}.py"
                path.write_text(SAMPLE, encoding="utf-8")
                result = function(str(path), DUPLICATED, "    value = 2")
            finally:
                _TOOL_REGISTRY.clear()
                _TOOL_REGISTRY.update(saved)
            # ``operation`` is file_tools' own pre-existing extra key.
            shapes[tool_id] = frozenset(result) - {"operation"}

        distinct = set(shapes.values())
        assert len(distinct) == 1, f"edit tools disagree on error keys: {shapes}"
