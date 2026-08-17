# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Code file parsers for the GAIA code index.

Supports Python (AST-based), JS/TS/Go/Rust/Java/C/C++ (regex-based),
and a fallback block-based splitter for unknown file types.
"""

import ast
import os
import re
from typing import List

from .sdk import CodeChunk

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

_EXT_TO_LANG = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
}


def detect_language(filename: str) -> str:
    """Return the language identifier for *filename* based on its extension."""
    ext = os.path.splitext(filename)[1].lower()
    return _EXT_TO_LANG.get(ext, "text")


# ---------------------------------------------------------------------------
# Binary detection
# ---------------------------------------------------------------------------

_BINARY_CHECK_BYTES = 8192


def is_binary_file(path: str) -> bool:
    """Return True if the file at *path* appears to be binary (contains null bytes)."""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_BINARY_CHECK_BYTES)
        return b"\x00" in chunk
    except OSError:
        return False


def _has_null_bytes(content: str) -> bool:
    return "\x00" in content


# ---------------------------------------------------------------------------
# Python AST parser
# ---------------------------------------------------------------------------


def parse_python_file(file_path: str, content: str) -> List[CodeChunk]:
    """Parse *content* of a Python file using AST; fall back to generic on error."""
    if not content.strip():
        return []

    try:
        tree = ast.parse(content)
    except SyntaxError:
        return parse_generic_file(file_path, content, "python")

    lines = content.splitlines()
    chunks: List[CodeChunk] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbol_type = "class" if isinstance(node, ast.ClassDef) else "function"
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            node_lines = lines[start - 1 : end]
            text = "\n".join(node_lines)
            docstring = ast.get_docstring(node)
            chunks.append(
                CodeChunk(
                    file_path=file_path,
                    content=text,
                    start_line=start,
                    end_line=end,
                    language="python",
                    symbol_name=node.name,
                    symbol_type=symbol_type,
                    docstring=docstring,
                )
            )

    # If no top-level symbols, emit the whole file as one chunk
    if not chunks:
        chunks.append(
            CodeChunk(
                file_path=file_path,
                content=content,
                start_line=1,
                end_line=len(lines),
                language="python",
            )
        )

    return chunks


# ---------------------------------------------------------------------------
# Generic regex-based parser
# ---------------------------------------------------------------------------

# Per-language patterns: each entry is (symbol_type, compiled_regex)
# The regex must have a named group `name`.
_LANG_PATTERNS: dict = {
    "javascript": [
        (
            "function",
            re.compile(
                r"^(?:export\s+)?(?:async\s+)?function\s+(?P<name>\w+)", re.MULTILINE
            ),
        ),
        (
            "function",
            re.compile(
                r"^(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s+)?\(",
                re.MULTILINE,
            ),
        ),
        ("class", re.compile(r"^(?:export\s+)?class\s+(?P<name>\w+)", re.MULTILINE)),
    ],
    "typescript": [
        (
            "function",
            re.compile(
                r"^(?:export\s+)?(?:async\s+)?function\s+(?P<name>\w+)", re.MULTILINE
            ),
        ),
        (
            "function",
            re.compile(
                r"^(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s+)?(?:\(|<)",
                re.MULTILINE,
            ),
        ),
        (
            "class",
            re.compile(
                r"^(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>\w+)", re.MULTILINE
            ),
        ),
        (
            "interface",
            re.compile(r"^(?:export\s+)?interface\s+(?P<name>\w+)", re.MULTILINE),
        ),
    ],
    "go": [
        (
            "function",
            re.compile(
                r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(?P<name>\w+)\s*\(", re.MULTILINE
            ),
        ),
        ("struct", re.compile(r"^type\s+(?P<name>\w+)\s+struct\b", re.MULTILINE)),
        ("interface", re.compile(r"^type\s+(?P<name>\w+)\s+interface\b", re.MULTILINE)),
    ],
    "rust": [
        (
            "function",
            re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>\w+)", re.MULTILINE),
        ),
        ("struct", re.compile(r"^(?:pub\s+)?struct\s+(?P<name>\w+)", re.MULTILINE)),
        ("enum", re.compile(r"^(?:pub\s+)?enum\s+(?P<name>\w+)", re.MULTILINE)),
        (
            "impl",
            re.compile(r"^(?:pub\s+)?impl(?:<[^>]+>)?\s+(?P<name>\w+)", re.MULTILINE),
        ),
    ],
    "java": [
        (
            "function",
            re.compile(
                r"^\s+(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+(?P<name>\w+)\s*\(",
                re.MULTILINE,
            ),
        ),
        (
            "class",
            re.compile(
                r"^(?:public\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+(?P<name>\w+)",
                re.MULTILINE,
            ),
        ),
    ],
    "c": [
        (
            "function",
            re.compile(
                r"^(?:static\s+)?(?:inline\s+)?[\w\*]+\s+(?P<name>\w+)\s*\([^;]*\)\s*\{",
                re.MULTILINE,
            ),
        ),
    ],
    "cpp": [
        (
            "function",
            re.compile(
                r"^(?:static\s+)?(?:inline\s+)?(?:virtual\s+)?[\w\*:<>]+\s+(?P<name>\w+)\s*\([^;]*\)\s*(?:const\s*)?\{",
                re.MULTILINE,
            ),
        ),
        ("class", re.compile(r"^(?:class|struct)\s+(?P<name>\w+)", re.MULTILINE)),
    ],
}


def parse_generic_file(file_path: str, content: str, language: str) -> List[CodeChunk]:
    """Parse *content* using regex patterns for *language*.

    Falls back to paragraph/block splitting for unknown languages.
    """
    if not content.strip():
        return []

    lines = content.splitlines()
    patterns = _LANG_PATTERNS.get(language)

    if patterns:
        return _parse_with_patterns(file_path, content, lines, language, patterns)

    # Unknown language — split on blank-line blocks
    return _chunk_by_blocks(file_path, content, lines, language)


def _parse_with_patterns(
    file_path: str,
    content: str,
    lines: List[str],
    language: str,
    patterns: list,
) -> List[CodeChunk]:
    """Extract symbol-level chunks using regex patterns."""
    # Collect all match positions
    matches = []
    for symbol_type, pattern in patterns:
        for m in pattern.finditer(content):
            matches.append((m.start(), m.end(), symbol_type, m.group("name")))

    if not matches:
        # No symbols found — emit file as one chunk
        return [
            CodeChunk(
                file_path=file_path,
                content=content,
                start_line=1,
                end_line=len(lines),
                language=language,
            )
        ]

    # Sort by position
    matches.sort(key=lambda x: x[0])

    chunks = []
    for i, (start_pos, _end_pos, symbol_type, name) in enumerate(matches):
        start_line = content[:start_pos].count("\n") + 1
        # End at the next symbol or EOF
        if i + 1 < len(matches):
            next_start = matches[i + 1][0]
            end_line = content[:next_start].count("\n")
        else:
            end_line = len(lines)

        chunk_content = "\n".join(lines[start_line - 1 : end_line])
        chunks.append(
            CodeChunk(
                file_path=file_path,
                content=chunk_content,
                start_line=start_line,
                end_line=end_line,
                language=language,
                symbol_name=name,
                symbol_type=symbol_type,
            )
        )

    return chunks


def _chunk_by_blocks(
    file_path: str,
    _content: str,
    lines: List[str],
    language: str,
    min_lines: int = 3,
    max_lines: int = 80,
) -> List[CodeChunk]:
    """Split content into chunks at blank-line boundaries."""
    chunks = []
    block_start = 0

    def _flush(start: int, end: int) -> None:
        block_lines = lines[start:end]
        text = "\n".join(block_lines).strip()
        if text.count("\n") + 1 >= min_lines:
            chunks.append(
                CodeChunk(
                    file_path=file_path,
                    content=text,
                    start_line=start + 1,
                    end_line=end,
                    language=language,
                )
            )

    for i, line in enumerate(lines):
        is_blank = not line.strip()
        block_len = i - block_start

        if (is_blank and block_len >= min_lines) or block_len >= max_lines:
            _flush(block_start, i)
            block_start = i + 1

    _flush(block_start, len(lines))
    return chunks


# ---------------------------------------------------------------------------
# Oversized-chunk guard
# ---------------------------------------------------------------------------

# How much of a chunk's text is actually embedded. Defined here (the SDK
# imports it) so the split size below can never drift from it again.
MAX_EMBED_CHARS = 1200

# A single pathological chunk (one giant minified line, a generated file with
# no blank lines for _chunk_by_blocks to split on) can otherwise become one
# CodeChunk holding the entire file: unbounded metadata.json growth and a
# mostly-unsearchable blob. Split at just under the embed window — a split
# part larger than what gets embedded defeats the split: at the previous
# 20,000 the guard's own stated goal failed, with ~94% of every part still
# unembedded. The margin leaves room for the "file: symbol:" prefix
# _chunk_to_embed_text prepends.
_MAX_CHUNK_CHARS = MAX_EMBED_CHARS - 100


def _split_oversized_chunk(
    chunk: CodeChunk, max_chars: int = _MAX_CHUNK_CHARS
) -> List[CodeChunk]:
    """Split *chunk* into character-bounded pieces if it exceeds *max_chars*.

    Each part carries its own line range, computed from the newlines that
    precede and span it — with the split threshold at the embed window,
    splitting is the common case, and every part sharing the whole block's
    range would make search hits point at the block instead of the fragment.
    """
    text = chunk.content
    if len(text) <= max_chars:
        return [chunk]

    n_parts = -(-len(text) // max_chars)  # ceil division
    parts = []
    for i in range(n_parts):
        lo = i * max_chars
        piece = text[lo : lo + max_chars]
        part_start = chunk.start_line + text.count("\n", 0, lo)
        part_end = min(chunk.end_line, part_start + piece.count("\n"))
        symbol_name = chunk.symbol_name
        if symbol_name:
            symbol_name = f"{symbol_name} (part {i + 1}/{n_parts})"
        parts.append(
            CodeChunk(
                content=piece,
                file_path=chunk.file_path,
                language=chunk.language,
                start_line=part_start,
                end_line=part_end,
                symbol_name=symbol_name,
                symbol_type=chunk.symbol_type,
                docstring=chunk.docstring if i == 0 else None,
                imports=chunk.imports,
            )
        )
    return parts


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def chunk_code_file(
    file_path: str,
    content: str,
    max_size_mb: float = 1.0,
) -> List[CodeChunk]:
    """Dispatch *content* to the appropriate parser and return chunks.

    Returns an empty list if the content is binary or exceeds *max_size_mb*.
    """
    # Size guard
    size_mb = len(content.encode("utf-8", errors="replace")) / (1024 * 1024)
    if size_mb > max_size_mb:
        return []

    # Binary guard
    if _has_null_bytes(content):
        return []

    language = detect_language(file_path)

    if language == "python":
        chunks = parse_python_file(file_path, content)
    else:
        chunks = parse_generic_file(file_path, content, language)

    result: List[CodeChunk] = []
    for c in chunks:
        result.extend(_split_oversized_chunk(c))
    return result
