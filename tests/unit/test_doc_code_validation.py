# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for util/check_doc_code.py — documentation code example validator."""

import textwrap
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _import_module():
    """Import the module under test (lives outside the package tree)."""
    import importlib
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    util_dir = str(repo_root / "util")
    if util_dir not in sys.path:
        sys.path.insert(0, util_dir)

    global check_doc_code
    check_doc_code = importlib.import_module("check_doc_code")


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
# check_code_blocks (integration)
# ---------------------------------------------------------------------------


class TestCheckCodeBlocks:
    """Integration tests for the full check pipeline."""

    def _setup_docs(self, tmp_path: Path, files: dict) -> str:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        for name, content in files.items():
            p = docs_dir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(tmp_path)

    def test_valid_python_passes(self, tmp_path):
        repo = self._setup_docs(
            tmp_path,
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
        results = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in results if r.status == "error"]
        assert len(errors) == 0

    def test_invalid_python_fails(self, tmp_path):
        repo = self._setup_docs(
            tmp_path,
            {
                "broken.mdx": """\
                    # Broken

                    ```python
                    def oops(
                    ```
                """,
            },
        )
        results = check_doc_code.check_code_blocks(repo)
        errors = [r for r in results if r.status == "error"]
        assert len(errors) == 1
        assert "SyntaxError" in errors[0].detail

    def test_bash_blocks_skipped_not_errored(self, tmp_path):
        repo = self._setup_docs(
            tmp_path,
            {
                "cli.mdx": """\
                    ```bash
                    gaia chat --ui
                    ```
                """,
            },
        )
        results = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in results if r.status == "error"]
        assert len(errors) == 0
        skipped = [r for r in results if r.status == "skipped"]
        assert len(skipped) == 1

    def test_lang_filter(self, tmp_path):
        repo = self._setup_docs(
            tmp_path,
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
        results = check_doc_code.check_code_blocks(
            repo, verbose=True, lang_filter="python"
        )
        assert all(r.lang == "python" for r in results)

    def test_multiple_files_scanned(self, tmp_path):
        repo = self._setup_docs(
            tmp_path,
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
        results = check_doc_code.check_code_blocks(repo, verbose=True)
        ok = [r for r in results if r.status == "ok"]
        assert len(ok) == 2

    def test_indented_code_in_mdx_component(self, tmp_path):
        """Code inside <Step>/<Tab> is indented — should still pass after dedent."""
        repo = self._setup_docs(
            tmp_path,
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
        results = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in results if r.status == "error"]
        assert len(errors) == 0

    def test_await_outside_function(self, tmp_path):
        """Top-level await is common in doc examples and should not error."""
        repo = self._setup_docs(
            tmp_path,
            {
                "async.mdx": (
                    "```python\n"
                    "result = await client.get('/api')\n"
                    "print(result)\n"
                    "```\n"
                ),
            },
        )
        results = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in results if r.status == "error"]
        assert len(errors) == 0

    def test_mixed_valid_and_invalid(self, tmp_path):
        repo = self._setup_docs(
            tmp_path,
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
        results = check_doc_code.check_code_blocks(repo, verbose=True)
        errors = [r for r in results if r.status == "error"]
        ok = [r for r in results if r.status == "ok"]
        assert len(errors) == 1
        assert len(ok) == 2


# ---------------------------------------------------------------------------
# format_results
# ---------------------------------------------------------------------------


class TestFormatResults:
    """Tests for terminal output formatting."""

    def test_passed_output(self):
        results = [
            check_doc_code.CodeResult("a.mdx", 1, "python", "ok", "syntax ok", None)
        ]
        output = check_doc_code.format_results(results)
        assert "PASSED" in output
        assert "OK:       1" in output

    def test_failed_output(self):
        results = [
            check_doc_code.CodeResult(
                "b.mdx",
                5,
                "python",
                "error",
                "SyntaxError:1: invalid syntax",
                None,
            )
        ]
        output = check_doc_code.format_results(results)
        assert "FAILED" in output
        assert "SYNTAX ERRORS" in output
        assert "b.mdx:5" in output

    def test_title_shown(self):
        results = [
            check_doc_code.CodeResult(
                "c.mdx",
                10,
                "python",
                "error",
                "SyntaxError:1: bad",
                "example.py",
            )
        ]
        output = check_doc_code.format_results(results)
        assert "(example.py)" in output


# ---------------------------------------------------------------------------
# main() exit code
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for CLI entry point."""

    def _setup_docs(self, tmp_path: Path, files: dict) -> str:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        for name, content in files.items():
            p = docs_dir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(tmp_path)

    def test_exit_zero_on_success(self, tmp_path, monkeypatch):
        repo = self._setup_docs(
            tmp_path,
            {"ok.mdx": "```python\nx = 1\n```\n"},
        )
        monkeypatch.setattr(
            check_doc_code.os.path,
            "abspath",
            lambda p: str(Path(repo) / "util" / "check_doc_code.py"),
        )
        code = check_doc_code.main([])
        assert code == 0

    def test_exit_one_on_errors(self, tmp_path, monkeypatch):
        repo = self._setup_docs(
            tmp_path,
            {"bad.mdx": "```python\ndef bad(\n```\n"},
        )
        monkeypatch.setattr(
            check_doc_code.os.path,
            "abspath",
            lambda p: str(Path(repo) / "util" / "check_doc_code.py"),
        )
        code = check_doc_code.main([])
        assert code == 1
