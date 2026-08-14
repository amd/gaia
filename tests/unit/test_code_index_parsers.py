# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Unit tests for gaia.code_index parsers module.
"""

import pytest

try:
    from gaia.code_index.parsers import (
        chunk_code_file,
        detect_language,
        is_binary_file,
        parse_generic_file,
        parse_python_file,
    )
    from gaia.code_index.sdk import CodeChunk

    PARSERS_AVAILABLE = True
except ImportError as e:
    PARSERS_AVAILABLE = False
    IMPORT_ERROR = str(e)


def skip_if_unavailable():
    if not PARSERS_AVAILABLE:
        pytest.skip(f"code_index not available: {IMPORT_ERROR}")


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_python(self):
        skip_if_unavailable()
        assert detect_language("foo.py") == "python"
        assert detect_language("foo.pyw") == "python"

    def test_javascript(self):
        skip_if_unavailable()
        assert detect_language("foo.js") == "javascript"
        assert detect_language("foo.jsx") == "javascript"

    def test_typescript(self):
        skip_if_unavailable()
        assert detect_language("foo.ts") == "typescript"
        assert detect_language("foo.tsx") == "typescript"

    def test_go(self):
        skip_if_unavailable()
        assert detect_language("foo.go") == "go"

    def test_rust(self):
        skip_if_unavailable()
        assert detect_language("foo.rs") == "rust"

    def test_java(self):
        skip_if_unavailable()
        assert detect_language("foo.java") == "java"

    def test_c(self):
        skip_if_unavailable()
        assert detect_language("foo.c") == "c"
        assert detect_language("foo.h") == "c"

    def test_cpp(self):
        skip_if_unavailable()
        assert detect_language("foo.cpp") == "cpp"
        assert detect_language("foo.hpp") == "cpp"

    def test_unknown_returns_text(self):
        skip_if_unavailable()
        assert detect_language("foo.xyz") == "text"
        assert detect_language("README.md") == "text"

    def test_case_insensitive(self):
        skip_if_unavailable()
        # Extension matching should be case-insensitive
        assert detect_language("FOO.PY") == "python"


# ---------------------------------------------------------------------------
# is_binary_file
# ---------------------------------------------------------------------------


class TestIsBinaryFile:
    def test_text_file_not_binary(self, tmp_path):
        skip_if_unavailable()
        f = tmp_path / "code.py"
        f.write_text("def foo(): pass\n")
        assert is_binary_file(str(f)) is False

    def test_file_with_null_bytes_is_binary(self, tmp_path):
        skip_if_unavailable()
        f = tmp_path / "binary.bin"
        f.write_bytes(b"some\x00content\x00here")
        assert is_binary_file(str(f)) is True

    def test_empty_file_not_binary(self, tmp_path):
        skip_if_unavailable()
        f = tmp_path / "empty.py"
        f.write_text("")
        assert is_binary_file(str(f)) is False

    def test_utf8_file_not_binary(self, tmp_path):
        skip_if_unavailable()
        f = tmp_path / "unicode.py"
        f.write_text("# -*- coding: utf-8 -*-\nx = '日本語'\n", encoding="utf-8")
        assert is_binary_file(str(f)) is False


# ---------------------------------------------------------------------------
# parse_python_file
# ---------------------------------------------------------------------------


PYTHON_SAMPLE = '''"""Module docstring."""

import os
import sys


def simple_function(x, y):
    """Add two numbers."""
    return x + y


async def async_function():
    """Async function."""
    pass


class MyClass:
    """A simple class."""

    def method(self):
        """A method."""
        pass

    @staticmethod
    def static_method():
        pass


def another_function():
    pass
'''


class TestParsePythonFile:
    def test_returns_list_of_code_chunks(self):
        skip_if_unavailable()
        chunks = parse_python_file("foo.py", PYTHON_SAMPLE)
        assert isinstance(chunks, list)
        assert len(chunks) > 0
        assert all(isinstance(c, CodeChunk) for c in chunks)

    def test_extracts_functions(self):
        skip_if_unavailable()
        chunks = parse_python_file("foo.py", PYTHON_SAMPLE)
        names = [c.symbol_name for c in chunks if c.symbol_type == "function"]
        assert "simple_function" in names
        assert "async_function" in names
        assert "another_function" in names

    def test_extracts_class(self):
        skip_if_unavailable()
        chunks = parse_python_file("foo.py", PYTHON_SAMPLE)
        class_chunks = [c for c in chunks if c.symbol_type == "class"]
        assert any(c.symbol_name == "MyClass" for c in class_chunks)

    def test_chunk_has_line_numbers(self):
        skip_if_unavailable()
        chunks = parse_python_file("foo.py", PYTHON_SAMPLE)
        for chunk in chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line

    def test_chunk_language_is_python(self):
        skip_if_unavailable()
        chunks = parse_python_file("foo.py", PYTHON_SAMPLE)
        assert all(c.language == "python" for c in chunks)

    def test_chunk_file_path_preserved(self):
        skip_if_unavailable()
        chunks = parse_python_file("src/foo.py", PYTHON_SAMPLE)
        assert all(c.file_path == "src/foo.py" for c in chunks)

    def test_syntax_error_falls_back_to_generic(self):
        skip_if_unavailable()
        bad_python = "def foo(:\n    pass\n"
        chunks = parse_python_file("bad.py", bad_python)
        # Falls back to generic parsing — must produce at least one chunk
        assert isinstance(chunks, list)

    def test_empty_file_returns_list(self):
        skip_if_unavailable()
        chunks = parse_python_file("empty.py", "")
        assert isinstance(chunks, list)

    def test_only_comments_returns_list(self):
        skip_if_unavailable()
        chunks = parse_python_file("comments.py", "# just a comment\n# another\n")
        assert isinstance(chunks, list)


# ---------------------------------------------------------------------------
# parse_generic_file
# ---------------------------------------------------------------------------


JS_SAMPLE = """\
function greet(name) {
    return "Hello, " + name;
}

class Greeter {
    constructor(name) {
        this.name = name;
    }

    greet() {
        return greet(this.name);
    }
}

const arrowFn = (x) => x * 2;
"""

GO_SAMPLE = """\
package main

import "fmt"

func main() {
    fmt.Println("hello")
}

func add(a, b int) int {
    return a + b
}
"""

RUST_SAMPLE = """\
fn main() {
    println!("hello");
}

pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

struct Point {
    x: f64,
    y: f64,
}
"""


class TestParseGenericFile:
    def test_javascript_extracts_functions(self):
        skip_if_unavailable()
        chunks = parse_generic_file("foo.js", JS_SAMPLE, "javascript")
        assert len(chunks) > 0
        assert all(isinstance(c, CodeChunk) for c in chunks)

    def test_javascript_language_set(self):
        skip_if_unavailable()
        chunks = parse_generic_file("foo.js", JS_SAMPLE, "javascript")
        assert all(c.language == "javascript" for c in chunks)

    def test_go_extracts_functions(self):
        skip_if_unavailable()
        chunks = parse_generic_file("foo.go", GO_SAMPLE, "go")
        assert len(chunks) > 0
        names = [c.symbol_name for c in chunks if c.symbol_name]
        assert any("main" in n or "add" in n for n in names)

    def test_rust_extracts_functions(self):
        skip_if_unavailable()
        chunks = parse_generic_file("foo.rs", RUST_SAMPLE, "rust")
        assert len(chunks) > 0

    def test_empty_content_returns_list(self):
        skip_if_unavailable()
        chunks = parse_generic_file("foo.go", "", "go")
        assert isinstance(chunks, list)

    def test_unknown_language_chunks_by_blocks(self):
        skip_if_unavailable()
        content = "block one\n\n\nblock two\n\n\nblock three"
        chunks = parse_generic_file("foo.xyz", content, "text")
        assert isinstance(chunks, list)
        assert len(chunks) > 0


# ---------------------------------------------------------------------------
# chunk_code_file (dispatcher)
# ---------------------------------------------------------------------------


class TestChunkCodeFile:
    def test_python_file_uses_ast_parser(self):
        skip_if_unavailable()
        chunks = chunk_code_file("foo.py", PYTHON_SAMPLE)
        assert len(chunks) > 0
        assert all(c.language == "python" for c in chunks)

    def test_js_file_uses_generic_parser(self):
        skip_if_unavailable()
        chunks = chunk_code_file("foo.js", JS_SAMPLE)
        assert len(chunks) > 0
        assert all(c.language == "javascript" for c in chunks)

    def test_go_file(self):
        skip_if_unavailable()
        chunks = chunk_code_file("foo.go", GO_SAMPLE)
        assert len(chunks) > 0

    def test_file_too_large_skipped(self, tmp_path):
        skip_if_unavailable()
        # chunk_code_file with content larger than 1MB should return empty
        large_content = "x = 1\n" * 200_000  # ~1.4MB
        chunks = chunk_code_file("huge.py", large_content, max_size_mb=1)
        assert chunks == []

    def test_binary_content_returns_empty(self, tmp_path):
        skip_if_unavailable()
        # If content has null bytes, treat as binary and return empty
        binary_content = "normal text\x00binary garbage\x00"
        chunks = chunk_code_file("data.bin", binary_content)
        assert chunks == []

    def test_non_utf8_fallback(self):
        skip_if_unavailable()
        # Latin-1 encoded content should not crash — return list (possibly empty)
        latin_content = "caf\xe9 = True\n"
        chunks = chunk_code_file("latin.py", latin_content)
        assert isinstance(chunks, list)

    def test_single_giant_line_is_split_not_one_unbounded_chunk(self):
        """A minified/generated file with no blank lines to split on must
        not become a single chunk holding the whole file — only the first
        ~1200 chars of a chunk are ever embedded, so the rest would be
        permanently unsearchable, and metadata.json would grow unbounded.
        """
        skip_if_unavailable()
        # One line, no newlines at all, ~250KB, well under the 1MB file cap.
        huge_single_line = "x=1;" * 50_000
        chunks = chunk_code_file("minified.js", huge_single_line)

        assert len(chunks) > 1, "a giant single line must be split into pieces"
        assert all(len(c.content) <= 20_000 for c in chunks)
        # Reassembling the pieces must reproduce the original content exactly
        # (no silent truncation or dropped bytes).
        assert "".join(c.content for c in chunks) == huge_single_line

    def test_oversized_symbol_chunk_is_split(self):
        """A single AST-parsed symbol (one enormous function) must also be
        split — the oversized-chunk guard isn't just for the no-symbols path.
        """
        skip_if_unavailable()
        huge_body = "\n".join(f"    x{i} = {i}" for i in range(10_000))
        source = f"def giant_function():\n{huge_body}\n    return x0\n"
        chunks = chunk_code_file("giant.py", source)

        assert len(chunks) > 1
        assert all(c.symbol_type == "function" for c in chunks)
        assert chunks[0].symbol_name.startswith("giant_function (part 1/")


# ---------------------------------------------------------------------------
# Chunking quality: symbol density, no-symbol files, deep nesting
# ---------------------------------------------------------------------------


MANY_SMALL_METHODS = """\
class Config:
    def get_a(self):
        return self.a

    def get_b(self):
        return self.b

    def get_c(self):
        return self.c

    def get_d(self):
        return self.d

    def get_e(self):
        return self.e
"""

DEEPLY_NESTED = """\
def outer():
    def middle():
        def inner():
            if True:
                for i in range(10):
                    while i > 0:
                        try:
                            with open("f") as fh:
                                return fh.read()
                        except OSError:
                            pass
        return inner
    return middle
"""

NO_FUNCTIONS_FILE = """\
API_URL = "https://example.com"
TIMEOUT = 30
RETRIES = [1, 2, 3]
CONFIG = {"key": "value"}
"""


class TestChunkingQuality:
    def test_class_with_many_small_methods_gets_one_chunk_per_method(self):
        """Each small method must be independently findable — a single
        chunk covering the whole class would bury unrelated methods behind
        each other in search results.
        """
        skip_if_unavailable()
        chunks = chunk_code_file("config.py", MANY_SMALL_METHODS)
        method_names = {c.symbol_name for c in chunks if c.symbol_type == "function"}
        assert method_names == {"get_a", "get_b", "get_c", "get_d", "get_e"}
        # Each chunk carries its own symbol name for context, not just body text.
        for c in chunks:
            assert c.symbol_name in c.content or c.symbol_name in method_names

    def test_file_with_no_functions_is_still_indexed_as_one_chunk(self):
        """A pure-constants/config file has no symbols to extract — it must
        still produce a searchable chunk instead of being silently dropped.
        """
        skip_if_unavailable()
        chunks = chunk_code_file("constants.py", NO_FUNCTIONS_FILE)
        assert len(chunks) == 1
        assert "API_URL" in chunks[0].content
        assert "RETRIES" in chunks[0].content

    def test_deeply_nested_function_captured_whole(self):
        """A deeply nested function must be captured as one chunk (by its
        outermost def), not truncated at the first nested block boundary.
        """
        skip_if_unavailable()
        chunks = chunk_code_file("nested.py", DEEPLY_NESTED)
        outer_chunks = [c for c in chunks if c.symbol_name == "outer"]
        assert len(outer_chunks) == 1
        # The whole nested body — down to the innermost except — is present.
        assert "except OSError" in outer_chunks[0].content
        assert "return fh.read()" in outer_chunks[0].content
