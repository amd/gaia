# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Documentation Code Example Validator

Scans all .mdx and .md files in docs/, extracts fenced code blocks,
and validates them:
  - Python blocks: checked with compile() for syntax errors
  - Import statements: optionally verified for module existence

Usage:
    python util/check_doc_code.py                    # Check all code blocks
    python util/check_doc_code.py --check-imports    # Also verify imports resolve
    python util/check_doc_code.py --verbose          # Show all blocks checked
    python util/check_doc_code.py --lang python      # Only check Python blocks
"""

import argparse
import importlib
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import List, NamedTuple, Optional, Sequence

# Matches opening code fence: ```lang title="..." or ```lang
FENCE_OPEN_RE = re.compile(
    r"^\s*```(?P<lang>\w[\w+-]*)(?:\s+title=[\"'](?P<title>[^\"']*)[\"'])?"
    r"(?:\s+.*)?$"
)
FENCE_CLOSE_RE = re.compile(r"^\s*```\s*$")

PYTHON_LANGS = {"python", "py", "python3"}
SKIP_LANGS = {
    "mermaid", "json", "yaml", "yml", "toml", "xml", "csv",
    "text", "txt", "md", "markdown", "mdx", "sql", "graphql",
    "diff", "ini", "conf", "cfg", "env", "properties",
}

IMPORT_RE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))")

# stdlib + common third-party that may not be in a lint environment
_KNOWN_MODULES = {
    "os", "sys", "re", "json", "time", "datetime", "pathlib", "typing",
    "collections", "functools", "itertools", "subprocess", "asyncio",
    "argparse", "logging", "unittest", "io", "math", "hashlib", "uuid",
    "shutil", "tempfile", "textwrap", "contextlib", "abc", "dataclasses",
    "enum", "copy", "glob", "signal", "threading", "multiprocessing",
    "socket", "http", "urllib", "importlib", "inspect", "traceback",
    "warnings", "pprint", "struct", "base64", "hmac", "secrets",
    "csv", "sqlite3", "xml", "html", "email", "mimetypes",
    "requests", "flask", "django", "fastapi", "uvicorn", "starlette",
    "numpy", "pandas", "scipy", "matplotlib", "sklearn", "torch",
    "openai", "anthropic", "httpx", "pydantic", "pytest", "rich",
    "click", "typer", "jinja2", "yaml", "toml", "dotenv",
    "PIL", "cv2", "transformers", "huggingface_hub",
    "docker", "redis", "celery", "boto3", "aiohttp", "websockets",
    "blender", "bpy",
}


class CodeBlock(NamedTuple):
    file: str
    line: int
    lang: str
    source: str
    title: Optional[str]


class CodeResult(NamedTuple):
    file: str
    line: int
    lang: str
    status: str  # "ok", "error", "warning", "skipped"
    detail: str
    title: Optional[str]


def find_doc_files(repo_root: str) -> List[Path]:
    """Find all documentation files to scan for code blocks."""
    files = []
    docs_dir = Path(repo_root) / "docs"
    if docs_dir.exists():
        for ext in ("*.mdx", "*.md"):
            files.extend(docs_dir.rglob(ext))
    return sorted(files)


def extract_code_blocks(filepath: Path, repo_root: Path) -> List[CodeBlock]:
    """Extract all fenced code blocks from a documentation file."""
    blocks: List[CodeBlock] = []
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return blocks

    lines = content.splitlines()
    rel_path = str(filepath.relative_to(repo_root))
    i = 0
    while i < len(lines):
        match = FENCE_OPEN_RE.match(lines[i])
        if match:
            lang = match.group("lang")
            title = match.group("title")
            fence_start = i + 1  # 1-based
            source_lines: List[str] = []
            i += 1
            while i < len(lines):
                if FENCE_CLOSE_RE.match(lines[i]):
                    break
                source_lines.append(lines[i])
                i += 1
            blocks.append(
                CodeBlock(
                    file=rel_path,
                    line=fence_start,
                    lang=lang.lower(),
                    source="\n".join(source_lines),
                    title=title,
                )
            )
        i += 1
    return blocks


def _normalize_python_source(source: str) -> str:
    """Normalize Python source for syntax checking.

    - ``textwrap.dedent`` strips MDX-nesting indentation
    - Standalone ``...`` (common doc placeholder) becomes ``pass``
    - Top-level ``await`` gets wrapped in ``async def``
    - Top-level ``return``/``yield``/``nonlocal`` gets wrapped in ``def``
    """
    # Strip common leading whitespace from MDX component nesting
    source = textwrap.dedent(source)

    normalized: List[str] = []
    for line in source.splitlines():
        if line.strip() == "...":
            indent = line[: len(line) - len(line.lstrip())]
            normalized.append(f"{indent}pass")
        else:
            normalized.append(line)
    text = "\n".join(normalized)

    # Wrap top-level await/async-for/async-with in async def
    if re.search(r"(?:^|\s)await\s|^async (?:for|with) ", text, re.MULTILINE):
        text = "async def _doc_wrapper():\n" + textwrap.indent(text, "    ")

    # Wrap code containing return/yield/nonlocal (method body excerpts) in def
    elif re.search(r"^\s*(return\b|yield\b|nonlocal\b)", text, re.MULTILINE):
        text = "def _doc_wrapper():\n" + textwrap.indent(text, "    ")

    return text


def check_python_syntax(source: str, filename: str = "<doc>") -> Optional[str]:
    """Return None if *source* compiles, or an error message string."""
    normalized = _normalize_python_source(source)
    if not normalized.strip():
        return None
    try:
        compile(normalized, filename, "exec")
        return None
    except SyntaxError as e:
        col = f":{e.lineno}" if e.lineno else ""
        return f"SyntaxError{col}: {e.msg}"


def check_imports(source: str) -> List[str]:
    """Best-effort check that imported top-level modules exist."""
    warnings: List[str] = []
    for line in source.splitlines():
        m = IMPORT_RE.match(line)
        if not m:
            continue
        module = m.group(1) or m.group(2)
        top_level = module.split(".")[0]
        if top_level in _KNOWN_MODULES:
            continue
        try:
            importlib.import_module(top_level)
        except ImportError:
            warnings.append(f"import {module}: top-level module '{top_level}' not found")
    return warnings


def check_code_blocks(
    repo_root: str,
    check_imports_flag: bool = False,
    verbose: bool = False,
    lang_filter: Optional[str] = None,
) -> List[CodeResult]:
    """Check all code blocks in documentation files."""
    root = Path(repo_root)
    results: List[CodeResult] = []

    for filepath in find_doc_files(repo_root):
        for block in extract_code_blocks(filepath, root):
            if lang_filter and block.lang != lang_filter.lower():
                continue

            if block.lang in PYTHON_LANGS:
                err = check_python_syntax(block.source, f"{block.file}:{block.line}")
                if err:
                    results.append(CodeResult(
                        block.file, block.line, block.lang,
                        "error", err, block.title,
                    ))
                else:
                    if check_imports_flag:
                        for w in check_imports(block.source):
                            results.append(CodeResult(
                                block.file, block.line, block.lang,
                                "warning", w, block.title,
                            ))
                    if verbose:
                        results.append(CodeResult(
                            block.file, block.line, block.lang,
                            "ok", "syntax ok", block.title,
                        ))

            elif block.lang in SKIP_LANGS:
                if verbose:
                    results.append(CodeResult(
                        block.file, block.line, block.lang,
                        "skipped", "no validator for this language", block.title,
                    ))
            else:
                if verbose:
                    results.append(CodeResult(
                        block.file, block.line, block.lang,
                        "skipped", f"no syntax checker for '{block.lang}'",
                        block.title,
                    ))

    return sorted(results, key=lambda r: (r.status != "error", r.file, r.line))


def format_results(results: List[CodeResult], verbose: bool = False) -> str:
    """Format results for terminal output."""
    lines: List[str] = []
    errors = [r for r in results if r.status == "error"]
    warnings = [r for r in results if r.status == "warning"]
    ok_count = sum(1 for r in results if r.status == "ok")
    skipped_count = sum(1 for r in results if r.status == "skipped")

    if errors:
        lines.append("SYNTAX ERRORS:")
        lines.append("=" * 80)
        for r in errors:
            title_suffix = f" ({r.title})" if r.title else ""
            lines.append(f"  {r.file}:{r.line}{title_suffix}")
            lines.append(f"    Language: {r.lang}")
            lines.append(f"    Error: {r.detail}")
            lines.append("")

    if warnings:
        lines.append("WARNINGS:")
        lines.append("=" * 80)
        for r in warnings:
            title_suffix = f" ({r.title})" if r.title else ""
            lines.append(f"  {r.file}:{r.line}{title_suffix}")
            lines.append(f"    {r.detail}")
            lines.append("")

    lines.append("=" * 80)
    lines.append(f"Code blocks checked: {ok_count + len(errors) + len(warnings)}")
    lines.append(f"  OK:       {ok_count}")
    lines.append(f"  Errors:   {len(errors)}")
    lines.append(f"  Warnings: {len(warnings)}")
    lines.append(f"  Skipped:  {skipped_count}")

    if errors:
        lines.append("")
        lines.append("FAILED: Found syntax errors in documentation code examples")
    else:
        lines.append("")
        lines.append("PASSED: All code examples are syntactically valid")

    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate code examples in documentation"
    )
    parser.add_argument(
        "--check-imports",
        action="store_true",
        help="Also verify that imported modules can be resolved (best-effort)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all code blocks (including OK/skipped)",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default=None,
        help="Only check blocks of this language (e.g. python, bash)",
    )
    args = parser.parse_args(argv)

    # Ensure utf-8 output on Windows (cp1252 can't encode arrows in SyntaxError msgs)
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"Checking documentation code examples in: {repo_root}")
    print()

    results = check_code_blocks(
        repo_root,
        check_imports_flag=args.check_imports,
        verbose=args.verbose,
        lang_filter=args.lang,
    )

    print(format_results(results, verbose=args.verbose))

    errors = [r for r in results if r.status == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
