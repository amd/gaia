# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for the Claude-Code-style diff display: the shared unified-diff helper
(``gaia.agents.tools.diff_utils``) and its integration into the file-editing
tools the GAIA agent uses to mutate text files (``FileIOToolsMixin``).

Covers the four scenarios the diff-display feature must get right:
- large diffs get truncated for transport, not sent whole
- binary files are detected and skipped (a size summary, not a garbled diff)
- new file creation renders as a pure-addition diff
- clearing/deleting all of a file's content renders as a pure-deletion diff

All tests run without an LLM, a registry, or a running agent.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.tools.diff_utils import (
    DIFF_MAX_LINES,
    binary_skip_result,
    build_diff,
    diff_fields_for_overwrite,
    format_size,
    read_text_for_edit,
    read_text_or_binary,
)
from gaia.security import PathValidator

# ============================================================================
# 1. build_diff
# ============================================================================


class TestBuildDiff:
    def test_new_file_is_pure_addition(self):
        result = build_diff("new.txt", None, "line1\nline2\n")
        assert result["is_new_file"] is True
        assert result["has_changes"] is True
        assert result["additions"] == 2
        assert result["deletions"] == 0
        assert "new file, 2 lines" == result["summary"]
        # difflib marks the "before" side as /dev/null for a brand-new file.
        assert "/dev/null" in result["diff"]
        assert "+line1" in result["diff"]
        assert "+line2" in result["diff"]

    def test_ordinary_edit_counts_additions_and_deletions(self):
        before = "a\nb\nc\n"
        after = "a\nX\nc\n"
        result = build_diff("f.txt", before, after)
        assert result["is_new_file"] is False
        assert result["additions"] == 1
        assert result["deletions"] == 1
        assert result["summary"] == "+1 -1"
        assert "-b" in result["diff"]
        assert "+X" in result["diff"]

    def test_no_changes_produces_empty_diff(self):
        result = build_diff("f.txt", "same\n", "same\n")
        assert result["has_changes"] is False
        assert result["diff"] == ""
        assert result["summary"] == "no changes"

    def test_full_deletion_is_pure_removal(self):
        """Clearing a file's content end to end is representable as an all-'-' diff."""
        before = "line1\nline2\nline3\n"
        result = build_diff("f.txt", before, "")
        assert result["has_changes"] is True
        assert result["deletions"] == 3
        assert result["additions"] == 0
        assert result["summary"] == "+0 -3"
        for line in ("-line1", "-line2", "-line3"):
            assert line in result["diff"]
        content_lines = [
            line
            for line in result["diff"].splitlines()
            if not line.startswith(("---", "+++", "@@"))
        ]
        assert all(line.startswith("-") for line in content_lines)

    def test_diff_lines_are_not_double_spaced(self):
        """Regression: an earlier "\\n".join over already-newline-terminated
        difflib lines inserted a spurious blank line between every entry."""
        result = build_diff("f.txt", "a\nb\nc\n", "a\nX\nc\n")
        assert "\n\n" not in result["diff"].rstrip("\n")

    def test_large_diff_is_truncated_for_transport(self):
        before = "".join(f"line{i}\n" for i in range(DIFF_MAX_LINES + 500))
        after = "".join(f"line{i}-changed\n" for i in range(DIFF_MAX_LINES + 500))
        result = build_diff("big.txt", before, after, max_lines=100)
        assert result["diff_truncated"] is True
        rendered_lines = result["diff"].splitlines()
        # +1 for the appended truncation marker line.
        assert len(rendered_lines) <= 101
        assert "truncated for transport" in result["diff"]

    def test_small_diff_is_not_marked_truncated(self):
        result = build_diff("f.txt", "a\n", "b\n", max_lines=100)
        assert result["diff_truncated"] is False
        assert "truncated" not in result["diff"]

    def test_missing_trailing_newline_does_not_glue_lines(self):
        """difflib emits the final line bare when the source lacks a trailing
        newline; unjoined, "-beta" and "+beta" fused into "-beta+beta" and the
        card mis-parsed every later line number."""
        result = build_diff("f.py", "alpha\nbeta", "alpha\nbeta\ngamma\n")
        assert "-beta+beta" not in result["diff"]
        for line in result["diff"].splitlines():
            # exactly one diff marker per physical line
            assert not (line.startswith("-") and "+{}".format(line[1:]) in line)
        assert result["additions"] >= 1

    def test_diff_is_capped_by_bytes_not_just_lines(self):
        """4000 minified-JS-length lines blow the TUI's 1MB JSONL line cap and
        ride into the model's context; the byte ceiling bounds both."""
        long_line = "x" * 2000
        before = "\n".join(f"a{i}" for i in range(50)) + "\n"
        after = "\n".join(long_line + str(i) for i in range(50)) + "\n"
        result = build_diff("big.js", before, after)
        assert len(result["diff"].encode("utf-8")) < 12_000
        assert result["diff_truncated"] is True
        assert "truncated for transport" in result["diff"]

    def test_new_file_diff_is_a_short_preview(self):
        """A new file's diff is its whole content as additions — content the
        model already supplied. Only a preview rides back through the result."""
        big = "\n".join(f"line {i}" for i in range(500)) + "\n"
        result = build_diff("new.txt", None, big)
        assert result["is_new_file"] is True
        assert result["diff_truncated"] is True
        assert len(result["diff"].splitlines()) <= 45
        # counters still describe the WHOLE write, not the preview
        assert result["additions"] == 500
        assert "new file, 500 lines" == result["summary"]

    def test_context_lines_is_honored(self):
        before = "".join(f"l{i}\n" for i in range(20))
        after = before.replace("l10\n", "lX\n")
        tight = build_diff("f.txt", before, after, context_lines=1)
        wide = build_diff("f.txt", before, after, context_lines=5)
        assert len(wide["diff"]) > len(tight["diff"])


# ============================================================================
# 2. binary detection
# ============================================================================


class TestBinaryDetection:
    def test_read_text_or_binary_reads_text_file(self, tmp_path):
        f = tmp_path / "text.txt"
        f.write_text("hello", encoding="utf-8")
        text, size = read_text_or_binary(str(f))
        assert text == "hello"
        assert size is None

    def test_read_text_or_binary_reports_missing_file(self, tmp_path):
        text, size = read_text_or_binary(str(tmp_path / "nope.txt"))
        assert text is None
        assert size is None

    def test_read_text_or_binary_detects_binary(self, tmp_path):
        f = tmp_path / "image.png"
        # Invalid UTF-8 byte sequence.
        f.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01binarydata")
        text, size = read_text_or_binary(str(f))
        assert text is None
        assert size == f.stat().st_size

    def test_binary_skip_result_carries_size_and_summary(self):
        result = binary_skip_result(2_400_000)
        assert result["is_binary"] is True
        assert result["diff"] == ""
        # previous_*: merged into a write result AFTER the write's own
        # size_bytes, this must never clobber the bytes actually written.
        assert result["previous_size_bytes"] == 2_400_000
        assert "size_bytes" not in result
        assert "2.3 MB" in result["summary"]
        assert "skipped" in result["summary"]

    def test_diff_fields_for_overwrite_skips_diff_for_binary_original(self, tmp_path):
        f = tmp_path / "asset.bin"
        f.write_bytes(b"\x00\x01\xff\xfe\x80binary")
        fields = diff_fields_for_overwrite(str(f), "now text content\n")
        assert fields["is_binary"] is True
        assert fields["diff"] == ""

    def test_diff_fields_for_overwrite_diffs_text_original(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\n", encoding="utf-8")
        fields = diff_fields_for_overwrite(str(f), "x = 2\n")
        assert fields.get("is_binary") is not True
        assert fields["additions"] == 1
        assert fields["deletions"] == 1

    def test_diff_fields_for_overwrite_new_file(self, tmp_path):
        target = tmp_path / "brand_new.txt"
        fields = diff_fields_for_overwrite(str(target), "hello\n")
        assert fields["is_new_file"] is True

    def test_read_text_for_edit_returns_error_result_for_binary(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\xff\xfe\x00\x01\x80notutf8")
        text, error = read_text_for_edit(str(f))
        assert text is None
        assert error is not None
        assert error["status"] == "error"
        assert error["is_binary"] is True
        assert "write_file" in error["error"]

    def test_read_text_for_edit_returns_text_for_text_file(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("print(1)\n", encoding="utf-8")
        text, error = read_text_for_edit(str(f))
        assert error is None
        assert text == "print(1)\n"


class TestFormatSize:
    def test_bytes(self):
        assert format_size(500) == "500 B"

    def test_kilobytes(self):
        assert format_size(2048) == "2.0 KB"

    def test_megabytes(self):
        assert "MB" in format_size(5 * 1024 * 1024)


# ============================================================================
# 3. End-to-end through FileIOToolsMixin tools
# ============================================================================


@pytest.fixture
def mixin_and_registry(tmp_path):
    """A FileIOToolsMixin with a real PathValidator, tools freshly registered."""
    from gaia.agents.base.tools import _TOOL_REGISTRY
    from gaia.agents.tools.file_io_tools import FileIOToolsMixin

    mixin = FileIOToolsMixin()
    mixin.path_validator = PathValidator(allowed_paths=[str(tmp_path)])
    mixin.console = None
    mixin._validate_python_syntax = MagicMock(
        return_value={"is_valid": True, "errors": []}
    )
    mixin._parse_python_code = MagicMock()

    saved_registry = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        mixin.register_file_io_tools()
        yield mixin, _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved_registry)


def _fn(registry, name):
    fn = registry.get(name, {}).get("function")
    assert fn is not None, f"{name} tool not registered"
    return fn


class TestWriteFileDiffIntegration:
    def test_new_file_creation_carries_addition_diff(
        self, mixin_and_registry, tmp_path
    ):
        _, registry = mixin_and_registry
        write_fn = _fn(registry, "write_file")
        target = tmp_path / "fresh.txt"

        result = write_fn(file_path=str(target), content="hello\nworld\n")

        assert result["status"] == "success"
        assert result["is_new_file"] is True
        assert result["additions"] == 2
        assert "+hello" in result["diff"]

    def test_overwrite_carries_edit_diff(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        write_fn = _fn(registry, "write_file")
        target = tmp_path / "existing.txt"
        target.write_text("old line\n", encoding="utf-8")

        with patch.object(PathValidator, "_prompt_overwrite", return_value=True):
            result = write_fn(file_path=str(target), content="new line\n")

        assert result["status"] == "success"
        assert result["is_new_file"] is False
        assert "-old line" in result["diff"]
        assert "+new line" in result["diff"]

    def test_overwrite_of_binary_file_skips_diff(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        write_fn = _fn(registry, "write_file")
        target = tmp_path / "asset.dat"
        target.write_bytes(b"\x00\x01\xff\xfe\x80raw")

        with patch.object(PathValidator, "_prompt_overwrite", return_value=True):
            result = write_fn(file_path=str(target), content="now a text file\n")

        assert result["status"] == "success"
        assert result["is_binary"] is True
        assert result["diff"] == ""
        assert "skipped" in result["summary"]


class TestEditFileDiffIntegration:
    def test_edit_carries_diff_fields(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        edit_fn = _fn(registry, "edit_file")
        target = tmp_path / "app.txt"
        target.write_text("const x = 'old';\n", encoding="utf-8")

        result = edit_fn(file_path=str(target), old_content="old", new_content="new")

        assert result["status"] == "success"
        assert result["additions"] == 1
        assert result["deletions"] == 1
        assert "-const x = 'old';" in result["diff"]
        assert "+const x = 'new';" in result["diff"]

    def test_clearing_all_content_is_a_pure_deletion_diff(
        self, mixin_and_registry, tmp_path
    ):
        _, registry = mixin_and_registry
        edit_fn = _fn(registry, "edit_file")
        target = tmp_path / "doomed.txt"
        target.write_text("line1\nline2\n", encoding="utf-8")

        result = edit_fn(
            file_path=str(target), old_content="line1\nline2\n", new_content=""
        )

        assert result["status"] == "success"
        assert target.read_text(encoding="utf-8") == ""
        assert result["additions"] == 0
        assert result["deletions"] == 2

    def test_edit_binary_file_fails_with_size_summary(
        self, mixin_and_registry, tmp_path
    ):
        _, registry = mixin_and_registry
        edit_fn = _fn(registry, "edit_file")
        target = tmp_path / "photo.png"
        target.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01binarystuff")

        result = edit_fn(file_path=str(target), old_content="x", new_content="y")

        assert result["status"] == "error"
        assert result["is_binary"] is True
        assert result["size_bytes"] == target.stat().st_size
        # Never crashes with an opaque UnicodeDecodeError string.
        assert "UnicodeDecodeError" not in result["error"]


class TestWritePythonFileDiffIntegration:
    def test_new_python_file_is_pure_addition(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        write_fn = _fn(registry, "write_python_file")
        target = tmp_path / "mod.py"

        result = write_fn(file_path=str(target), content="x = 1\n")

        assert result["status"] == "success"
        assert result["is_new_file"] is True
        assert result["additions"] == 1


class TestEditPythonFileDiffIntegration:
    def test_edit_python_file_carries_diff_fields(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        edit_fn = _fn(registry, "edit_python_file")
        target = tmp_path / "mod.py"
        target.write_text("x = 1\n", encoding="utf-8")

        result = edit_fn(
            file_path=str(target), old_content="x = 1", new_content="x = 2"
        )

        assert result["status"] == "success"
        assert result["additions"] == 1
        assert result["deletions"] == 1
        assert "\n\n" not in result["diff"].rstrip("\n")

    def test_dry_run_reports_diff_without_writing(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        edit_fn = _fn(registry, "edit_python_file")
        target = tmp_path / "mod.py"
        target.write_text("x = 1\n", encoding="utf-8")

        result = edit_fn(
            file_path=str(target),
            old_content="x = 1",
            new_content="x = 2",
            dry_run=True,
        )

        assert result["dry_run"] is True
        assert result["would_change"] is True
        assert target.read_text(encoding="utf-8") == "x = 1\n"

    def test_edit_binary_python_path_fails_cleanly(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        edit_fn = _fn(registry, "edit_python_file")
        target = tmp_path / "weird.py"
        target.write_bytes(b"\xff\xfe\x00\x01\x80notutf8")

        result = edit_fn(file_path=str(target), old_content="x", new_content="y")

        assert result["status"] == "error"
        assert result["is_binary"] is True


class TestGenerateDiffToolIntegration:
    def test_generate_diff_new_file(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        fn = _fn(registry, "generate_diff")
        target = tmp_path / "brand_new.txt"

        result = fn(file_path=str(target), new_content="hello\n")

        assert result["status"] == "success"
        assert result["is_new_file"] is True
        assert result["additions"] == 1

    def test_generate_diff_binary_file_shows_size_summary(
        self, mixin_and_registry, tmp_path
    ):
        _, registry = mixin_and_registry
        fn = _fn(registry, "generate_diff")
        target = tmp_path / "asset.bin"
        target.write_bytes(b"\x00\x01\xff\xfe\x80raw")

        result = fn(file_path=str(target), new_content="text now\n")

        assert result["status"] == "success"
        assert result["is_binary"] is True
        assert result["diff"] == ""


class TestReplaceFunctionDiffIntegration:
    def test_replace_function_carries_diff_fields(self, mixin_and_registry, tmp_path):
        _, registry = mixin_and_registry
        fn = _fn(registry, "replace_function")
        target = tmp_path / "mod.py"
        target.write_text("def foo():\n    return 1\n", encoding="utf-8")

        result = fn(
            file_path=str(target),
            function_name="foo",
            new_implementation="def foo():\n    return 2\n",
        )

        assert result["status"] == "success"
        assert result["additions"] >= 1
        assert result["deletions"] >= 1
        assert "\n\n" not in result["diff"].rstrip("\n")


# ============================================================================
# 4. Full pipeline: tool result -> SSEOutputHandler -> CanonicalTranslator
#    -> the wire event the TUI's tui/internal/ui/chat/filediff.go reads.
#
# This is the regression guard for the actual defect this feature hit during
# development: SSEOutputHandler.pretty_print_json only forwards a tool's full
# result as `result_data` for a few special-cased shapes (file_list, chunks,
# a declared render card) — everything else collapses to a bare
# {summary, success} pair. Unit-testing diff_utils/file_io_tools alone would
# not have caught that the diff fields never reached the wire.
# ============================================================================


class TestDiffReachesTheCanonicalWireEvent:
    def _translate_one_tool_result(self, data):
        """Feed *data* through the same path stdio.py's run_turn uses:
        SSEOutputHandler.pretty_print_json -> CanonicalTranslator.translate.
        Returns the canonical tool_result event's `data` field.
        """
        from gaia.ui.sse_handler import SSEOutputHandler
        from gaia.ui.sse_translation import CanonicalTranslator

        handler = SSEOutputHandler()
        handler.print_tool_usage("write_file")
        handler.pretty_print_json(data, title="Result")

        translator = CanonicalTranslator(run_id=None, agent_id="gaia")
        canonical_events = []
        while not handler.event_queue.empty():
            raw_event = handler.event_queue.get_nowait()
            canonical_events.extend(translator.translate(raw_event))

        tool_results = [e for e in canonical_events if e.get("type") == "tool_result"]
        assert (
            len(tool_results) == 1
        ), f"expected exactly one tool_result, got {canonical_events}"
        return tool_results[0]["data"]

    def test_diff_field_survives_the_full_pipeline(self):
        result = {
            "status": "success",
            "file_path": "notes.md",
            "diff": "@@ -1 +1 @@\n-old\n+new\n",
            "additions": 1,
            "deletions": 1,
            "summary": "+1 -1",
        }
        wire_data = self._translate_one_tool_result(result)
        assert wire_data["diff"] == "@@ -1 +1 @@\n-old\n+new\n"
        assert wire_data["file_path"] == "notes.md"
        assert wire_data["status"] == "success"

    def test_binary_skip_fields_survive_the_full_pipeline(self):
        result = {
            "status": "success",
            "file_path": "logo.png",
            **binary_skip_result(2048),
        }
        wire_data = self._translate_one_tool_result(result)
        assert wire_data["is_binary"] is True
        assert wire_data["diff"] == ""

    def test_error_status_survives_the_full_pipeline(self):
        result = {"status": "error", "error": "Access denied", "diff": ""}
        wire_data = self._translate_one_tool_result(result)
        assert wire_data["status"] == "error"

    def test_ordinary_tool_result_carries_no_diff_key(self):
        """A tool with no `diff` field must not spuriously gain one — the
        generic {summary, success} fallback is what reaches the wire."""
        result = {"status": "success", "content": "hello world"}
        wire_data = self._translate_one_tool_result(result)
        assert "diff" not in wire_data


class TestHeaderVsContentDisambiguation:
    """The only header lines are the first two — content that HAPPENS to
    start with '---'/'+++' (a deleted SQL '-- x' comment, an added '++i;')
    is data, and both the counters and the card must keep it."""

    def test_deleted_sql_comment_lines_are_counted(self):
        before = "SELECT 1;\n-- old comment\n-- another\n"
        after = "SELECT 1;\n"
        result = build_diff("q.sql", before, after)
        assert result["deletions"] == 2
        assert result["additions"] == 0
        assert result["summary"] == "+0 -2"

    def test_added_increment_lines_are_counted(self):
        before = "int i;\n"
        after = "int i;\n++i;\n"
        result = build_diff("f.c", before, after)
        assert result["additions"] == 1
        assert result["summary"] == "+1 -0"
