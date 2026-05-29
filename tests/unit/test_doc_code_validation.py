# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for util/check_doc_code.py — documentation code example validator."""

import importlib
import sys
import textwrap
from pathlib import Path

import pytest

# Import module under test (lives outside the package tree)
_util_dir = str(Path(__file__).resolve().parents[2] / "util")
if _util_dir not in sys.path:
    sys.path.insert(0, _util_dir)

check_doc_code = importlib.import_module("check_doc_code")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def setup_docs(tmp_path):
    """Create a fake docs/ directory with the given files."""

    def _inner(files: dict) -> str:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        for name, content in files.items():
            p = docs_dir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(tmp_path)

    return _inner


# ---------------------------------------------------------------------------
# extract_code_blocks
# ---------------------------------------------------------------------------


class TestExtractCodeBlocks:
    """Tests for code block extraction from MDX content."""

    def _write_mdx(self, tmp_path: Path, content: str) -> Path:
        f = tmp_path / "test.mdx"
        f.write_text(textwrap.dedent(content), encoding="utf-8")
        return f

    def test_basic_python_block(self, tmp_path):
        f = self._write_mdx(
            tmp_path,
            """\
            # Title

            ```python
            print("hello")
            ```
            """,
        )
        blocks = check_doc_code.extract_code_blocks(f, tmp_path)
        assert len(blocks) == 1
        assert blocks[0].lang == "python"
        assert 'print("hello")' in blocks[0].source
        assert blocks[0].title is None

    def test_block_with_title(self, tmp_path):
        f = self._write_mdx(
            tmp_path,
            """\
            ```python title="example.py"
            x = 1
            ```
            """,
        )
        blocks = check_doc_code.extract_code_blocks(f, tmp_path)
        assert len(blocks) == 1
        assert blocks[0].title == "example.py"

    def test_multiple_languages(self, tmp_path):
        f = self._write_mdx(
            tmp_path,
            """\
            ```python
            x = 1
            ```

            ```bash
            echo hello
            ```

            ```json
            {"key": "value"}
            ```
            """,
        )
        blocks = check_doc_code.extract_code_blocks(f, tmp_path)
        assert len(blocks) == 3
        langs = [b.lang for b in blocks]
        assert langs == ["python", "bash", "json"]

    def test_nested_in_mdx_component(self, tmp_path):
        """Code blocks inside <Tabs>, <CodeGroup>, etc. are still extracted."""
        f = self._write_mdx(
            tmp_path,
            """\
            <Tabs>
              <Tab title="Python">
                ```python
                import os
                ```
              </Tab>
              <Tab title="Bash">
                ```bash
                ls -la
                ```
              </Tab>
            </Tabs>
            """,
        )
        blocks = check_doc_code.extract_code_blocks(f, tmp_path)
        assert len(blocks) == 2
        assert blocks[0].lang == "python"
        assert blocks[1].lang == "bash"

    def test_empty_block(self, tmp_path):
        f = self._write_mdx(
            tmp_path,
            """\
            ```python
            ```
            """,
        )
        blocks = check_doc_code.extract_code_blocks(f, tmp_path)
        assert len(blocks) == 1
        assert blocks[0].source == ""

    def test_line_numbers_are_1_based(self, tmp_path):
        f = self._write_mdx(
            tmp_path,
            """\
            line 1
            line 2
            ```python
            code here
            ```
            """,
        )
        blocks = check_doc_code.extract_code_blocks(f, tmp_path)
        assert blocks[0].line == 3  # fence opens on line 3 (1-based)

    def test_mermaid_block_extracted(self, tmp_path):
        f = self._write_mdx(
            tmp_path,
            """\
            ```mermaid
            flowchart TD
                A --> B
            ```
            """,
        )
        blocks = check_doc_code.extract_code_blocks(f, tmp_path)
        assert len(blocks) == 1
        assert blocks[0].lang == "mermaid"


# ---------------------------------------------------------------------------
# check_python_syntax
# ---------------------------------------------------------------------------


class TestCheckPythonSyntax:
    """Tests for Python syntax validation."""

    def test_valid_code(self):
        assert check_doc_code.check_python_syntax("x = 1\nprint(x)") is None

    def test_valid_function(self):
        code = textwrap.dedent("""\
            def greet(name: str) -> str:
                return f"Hello, {name}"
        """)
        assert check_doc_code.check_python_syntax(code) is None

    def test_valid_class(self):
        code = textwrap.dedent("""\
            class MyAgent:
                def __init__(self):
                    self.name = "test"

                def run(self):
                    return self.name
        """)
        assert check_doc_code.check_python_syntax(code) is None

    def test_syntax_error_detected(self):
        result = check_doc_code.check_python_syntax("def foo(")
        assert result is not None
        assert "SyntaxError" in result

    def test_missing_colon(self):
        result = check_doc_code.check_python_syntax("if True\n    pass")
        assert result is not None
        assert "SyntaxError" in result

    def test_ellipsis_placeholder_accepted(self):
        code = textwrap.dedent("""\
            def placeholder():
                ...
        """)
        assert check_doc_code.check_python_syntax(code) is None

    def test_empty_source(self):
        assert check_doc_code.check_python_syntax("") is None
        assert check_doc_code.check_python_syntax("   \n\n  ") is None

    def test_import_only(self):
        code = "from pathlib import Path\nimport os"
        assert check_doc_code.check_python_syntax(code) is None

    def test_multiline_string(self):
        code = textwrap.dedent('''\
            msg = """
            Hello
            World
            """
        ''')
        assert check_doc_code.check_python_syntax(code) is None

    def test_decorator(self):
        code = textwrap.dedent("""\
            @tool
            def search(query: str) -> str:
                \"\"\"Search for things.\"\"\"
                return query
        """)
        assert check_doc_code.check_python_syntax(code) is None


# ---------------------------------------------------------------------------
# _normalize_python_source
# ---------------------------------------------------------------------------


class TestNormalizePythonSource:
    """Tests for source normalization before syntax checking."""

    def test_ellipsis_becomes_pass(self):
        result = check_doc_code._normalize_python_source("def foo():\n    ...")
        assert "pass" in result
        assert "..." not in result

    def test_preserves_indentation(self):
        result = check_doc_code._normalize_python_source(
            "class C:\n    def m(self):\n        ..."
        )
        assert "        pass" in result

    def test_non_ellipsis_untouched(self):
        code = "x = 1\ny = 2"
        assert check_doc_code._normalize_python_source(code) == code

    def test_dedents_mdx_nesting(self):
        """Code blocks inside <Tab>/<Step> have leading whitespace."""
        code = "    from os import path\n    x = 1"
        result = check_doc_code._normalize_python_source(code)
        assert result.startswith("from os")

    def test_wraps_top_level_await(self):
        code = "result = await client.get('/api')\nprint(result)"
        result = check_doc_code._normalize_python_source(code)
        assert "async def _doc_wrapper" in result

    def test_wraps_top_level_return(self):
        code = "return some_value"
        result = check_doc_code._normalize_python_source(code)
        assert "def _doc_wrapper" in result

    def test_wraps_top_level_yield(self):
        code = "yield item"
        result = check_doc_code._normalize_python_source(code)
        assert "def _doc_wrapper" in result


# ---------------------------------------------------------------------------
# _is_pseudo_code
# ---------------------------------------------------------------------------


class TestIsPseudoCode:
    """Tests for pseudo-code / non-runnable block detection."""

    def test_function_signature_no_body(self):
        assert check_doc_code._is_pseudo_code("def foo(x: str) -> str") is True

    def test_multiline_signature_no_body(self):
        code = "def store(\n    self,\n    category: str,\n) -> str:"
        assert check_doc_code._is_pseudo_code(code) is True

    def test_arrow_flow_diagram(self):
        code = "Agent.__init__()\n  \u2192 Load system prompt\n  \u2192 Register tools"
        assert check_doc_code._is_pseudo_code(code) is True

    def test_dict_with_trailing_ellipsis(self):
        code = '{"time": "14:32", "platform": "Windows", ...}'
        assert check_doc_code._is_pseudo_code(code) is True

    def test_mixed_python_and_toml(self):
        code = 'from gaia import Agent\n\n[project.entry-points."gaia.agents"]\nmy = "pkg:Cls"'
        assert check_doc_code._is_pseudo_code(code) is True

    def test_mixed_python_and_shell(self):
        code = "# Install deps\nuv pip install -e '.[rag]'\nimport os"
        assert check_doc_code._is_pseudo_code(code) is True

    def test_real_code_not_pseudo(self):
        code = "x = 1\nprint(x)"
        assert check_doc_code._is_pseudo_code(code) is False

    def test_class_with_body_not_pseudo(self):
        code = "class Foo:\n    def bar(self):\n        pass"
        assert check_doc_code._is_pseudo_code(code) is False

    def test_incomplete_def_not_pseudo(self):
        """def foo( with no closing paren is broken syntax, not pseudo-code."""
        assert check_doc_code._is_pseudo_code("def foo(") is False


# ---------------------------------------------------------------------------
# check_code_blocks (integration)
# ---------------------------------------------------------------------------


class TestCheckCodeBlocks:
    """Integration tests for the full check pipeline."""

    def test_valid_python_passes(self, setup_docs):
        repo = setup_docs(
            {
                "guide.mdx": """\
                    # Guide

                    ```python
                    x = 1
                    print(x)
                    ```
                """,
            },
        )
        summary = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in summary.results if r.status == "error"]
        assert len(errors) == 0

    def test_invalid_python_fails(self, setup_docs):
        repo = setup_docs(
            {
                "broken.mdx": """\
                    # Broken

                    ```python
                    def oops(
                    ```
                """,
            },
        )
        summary = check_doc_code.check_code_blocks(repo)
        errors = [r for r in summary.results if r.status == "error"]
        assert len(errors) == 1
        assert "SyntaxError" in errors[0].detail

    def test_bash_blocks_skipped_not_errored(self, setup_docs):
        repo = setup_docs(
            {
                "cli.mdx": """\
                    ```bash
                    gaia chat --ui
                    ```
                """,
            },
        )
        summary = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in summary.results if r.status == "error"]
        assert len(errors) == 0
        assert summary.skipped_count == 1

    def test_lang_filter(self, setup_docs):
        repo = setup_docs(
            {
                "multi.mdx": """\
                    ```python
                    x = 1
                    ```

                    ```bash
                    echo hi
                    ```
                """,
            },
        )
        summary = check_doc_code.check_code_blocks(
            repo, verbose=True, lang_filter="python"
        )
        assert all(r.lang == "python" for r in summary.results)

    def test_multiple_files_scanned(self, setup_docs):
        repo = setup_docs(
            {
                "a.mdx": """\
                    ```python
                    x = 1
                    ```
                """,
                "sub/b.mdx": """\
                    ```python
                    y = 2
                    ```
                """,
            },
        )
        summary = check_doc_code.check_code_blocks(repo, verbose=True)
        assert summary.ok_count == 2

    def test_indented_code_in_mdx_component(self, setup_docs):
        """Code inside <Step>/<Tab> is indented — should still pass after dedent."""
        repo = setup_docs(
            {
                "steps.mdx": (
                    "<Steps>\n"
                    "  <Step>\n"
                    "    ```python\n"
                    "    from pathlib import Path\n"
                    "    x = Path('.')\n"
                    "    ```\n"
                    "  </Step>\n"
                    "</Steps>\n"
                ),
            },
        )
        summary = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in summary.results if r.status == "error"]
        assert len(errors) == 0

    def test_await_outside_function(self, setup_docs):
        """Top-level await is common in doc examples and should not error."""
        repo = setup_docs(
            {
                "async.mdx": (
                    "```python\n"
                    "result = await client.get('/api')\n"
                    "print(result)\n"
                    "```\n"
                ),
            },
        )
        summary = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in summary.results if r.status == "error"]
        assert len(errors) == 0

    def test_mixed_valid_and_invalid(self, setup_docs):
        repo = setup_docs(
            {
                "mixed.mdx": """\
                    ```python
                    x = 1
                    ```

                    ```python
                    def bad(
                    ```

                    ```python
                    y = 2
                    ```
                """,
            },
        )
        summary = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in summary.results if r.status == "error"]
        assert len(errors) == 1
        assert summary.ok_count == 2

    def test_ok_count_without_verbose(self, setup_docs):
        """ok_count must be accurate even without --verbose."""
        repo = setup_docs(
            {
                "two.mdx": "```python\nx = 1\n```\n\n```python\ny = 2\n```\n",
            },
        )
        summary = check_doc_code.check_code_blocks(repo, verbose=False)
        assert summary.ok_count == 2
        # Without verbose, no "ok" results are in the list
        assert not any(r.status == "ok" for r in summary.results)


# ---------------------------------------------------------------------------
# format_results
# ---------------------------------------------------------------------------


class TestFormatResults:
    """Tests for terminal output formatting."""

    def test_passed_output(self):
        summary = check_doc_code.CheckSummary(
            results=[],
            ok_count=3,
            skipped_count=0,
        )
        output = check_doc_code.format_results(summary)
        assert "PASSED" in output
        assert "OK:       3" in output

    def test_failed_output(self):
        summary = check_doc_code.CheckSummary(
            results=[
                check_doc_code.CodeResult(
                    "b.mdx",
                    5,
                    "python",
                    "error",
                    "SyntaxError:1: invalid syntax",
                    None,
                )
            ],
            ok_count=0,
            skipped_count=0,
        )
        output = check_doc_code.format_results(summary)
        assert "FAILED" in output
        assert "SYNTAX ERRORS" in output
        assert "b.mdx:5" in output

    def test_title_shown(self):
        summary = check_doc_code.CheckSummary(
            results=[
                check_doc_code.CodeResult(
                    "c.mdx",
                    10,
                    "python",
                    "error",
                    "SyntaxError:1: bad",
                    "example.py",
                )
            ],
            ok_count=0,
            skipped_count=0,
        )
        output = check_doc_code.format_results(summary)
        assert "(example.py)" in output


# ---------------------------------------------------------------------------
# main() exit code
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for CLI entry point."""

    def test_exit_zero_on_success(self, setup_docs, monkeypatch):
        repo = setup_docs({"ok.mdx": "```python\nx = 1\n```\n"})
        monkeypatch.setattr(
            check_doc_code.os.path,
            "abspath",
            lambda p: str(Path(repo) / "util" / "check_doc_code.py"),
        )
        code = check_doc_code.main([])
        assert code == 0

    def test_exit_one_on_errors(self, setup_docs, monkeypatch):
        repo = setup_docs({"bad.mdx": "```python\ndef bad(\n```\n"})
        monkeypatch.setattr(
            check_doc_code.os.path,
            "abspath",
            lambda p: str(Path(repo) / "util" / "check_doc_code.py"),
        )
        code = check_doc_code.main([])
        assert code == 1
