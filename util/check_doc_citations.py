# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Documentation Citation Checker

Parses all .mdx and .md files in docs/ and verifies that `path:NNN` style
citations to source files actually resolve. Two checks are performed:

    (a) File existence + line-range bounds — the referenced file exists
        and NNN falls within its line count.

    (b) Symbol-anchor assertion — for high-risk "landmark" citations
        (NSIS macros, class definitions), verify the expected symbol
        still appears within a small window of the cited line. Catches
        "symbol moved but line still within file bounds" drift that
        plain bounds checks miss.

Citations recognized:
    - `path/to/file.py:123`               — plain cite, line 123
    - `path/to/file.py:123-456`           — plain cite, range
    - [text](path/to/file.py:123)         — markdown link with line anchor
    - Backticked forms inside MDX content

Usage:
    python util/check_doc_citations.py              # Check all docs
    python util/check_doc_citations.py --verbose    # Show all citations
    python util/check_doc_citations.py --paths docs/guides/custom-installer.mdx

Exit code is non-zero if any citation fails.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOC_DIRS = ["docs"]

# Citation forms. All patterns capture (path, line_start, line_end_or_none).
# Only paths that look like repo-relative source/infra references are checked.
CITE_EXT = r"(?:py|ts|tsx|js|jsx|json|ya?ml|nsh|nsi|ps1|toml|md|mdx|sh|bat|cfg|ini)"
PATH_PREFIX = r"(?:src|installer|util|scripts|\.github|tests|docs|cpp|examples|workshop)"

# Backticked `path/to/file.ext:NNN` or `path/to/file.ext:NNN-MMM`
BACKTICK_CITE_RE = re.compile(
    rf"`({PATH_PREFIX}/[^\s`]+\.{CITE_EXT}):(\d+)(?:-(\d+))?`"
)

# Markdown link target `(path/to/file:NNN)` — less common but supported
LINK_CITE_RE = re.compile(
    rf"\(({PATH_PREFIX}/[^\s)]+\.{CITE_EXT}):(\d+)(?:-(\d+))?\)"
)

# Symbol anchors — hard-coded expectations for high-risk landmarks.
# Maps repo-relative path -> {line_number: regex_expected_within_window}.
# A citation of `<path>:NNN` where NNN is within ±WINDOW of a key triggers
# a regex check on that line.
ANCHOR_WINDOW = 3
ANCHORS = {
    "installer/nsis/installer.nsh": {
        56: r"^!macro\s+customInstall\b",
        69: r"^!macro\s+customUnInstall\b",
    },
    "src/gaia/agents/registry.py": {
        37: r"^class\s+AgentManifest\b",
        86: r"^class\s+AgentRegistry\b",
    },
    "src/gaia/agents/base/agent.py": {
        51: r"^class\s+Agent\b",
    },
    "src/gaia/installer/export_import.py": {
        125: r"^def\s+export_custom_agents\b",
        272: r"^def\s+import_agent_bundle\b",
    },
    "src/gaia/apps/webui/services/agent-seeder.cjs": {
        225: r"^async\s+function\s+seedBundledAgents\b",
    },
}


class CiteResult(NamedTuple):
    doc: str
    doc_line: int
    target: str
    line_start: int
    line_end: Optional[int]
    status: str  # "ok", "missing-file", "out-of-bounds", "symbol-drift"
    detail: str


def iter_docs(paths: List[Path]):
    for base in paths:
        if base.is_file() and base.suffix.lower() in {".md", ".mdx"}:
            yield base
            continue
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix.lower() in {".md", ".mdx"} and p.is_file():
                yield p


def extract_cites(doc: Path):
    """Yield (doc_line, target_path, start, end_or_None) for every cite."""
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in BACKTICK_CITE_RE.finditer(line):
            path, start, end = m.group(1), int(m.group(2)), m.group(3)
            yield (lineno, path, start, int(end) if end else None)
        for m in LINK_CITE_RE.finditer(line):
            path, start, end = m.group(1), int(m.group(2)), m.group(3)
            yield (lineno, path, start, int(end) if end else None)


def check_cite(
    doc_rel: str,
    doc_line: int,
    target: str,
    start: int,
    end: Optional[int],
) -> CiteResult:
    target_path = REPO_ROOT / target
    if not target_path.exists():
        return CiteResult(
            doc_rel, doc_line, target, start, end,
            "missing-file", f"{target} does not exist",
        )
    try:
        total_lines = sum(1 for _ in target_path.open("rb"))
    except OSError as exc:
        return CiteResult(
            doc_rel, doc_line, target, start, end,
            "missing-file", f"cannot read {target}: {exc}",
        )
    hi = end or start
    if start < 1 or hi > total_lines or start > hi:
        return CiteResult(
            doc_rel, doc_line, target, start, end,
            "out-of-bounds",
            f"{target} has {total_lines} lines; cite {start}"
            + (f"-{end}" if end else "")
            + " is out of bounds",
        )

    # Symbol-anchor assertion for landmarks.
    anchors = ANCHORS.get(target)
    if anchors:
        try:
            file_lines = target_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            file_lines = []
        for anchor_line, pattern in anchors.items():
            if abs(anchor_line - start) > ANCHOR_WINDOW:
                continue
            lo = max(1, anchor_line - ANCHOR_WINDOW)
            hi_w = min(total_lines, anchor_line + ANCHOR_WINDOW)
            window = "\n".join(file_lines[lo - 1:hi_w])
            if not re.search(pattern, window, re.MULTILINE):
                return CiteResult(
                    doc_rel, doc_line, target, start, end,
                    "symbol-drift",
                    f"expected /{pattern}/ within ±{ANCHOR_WINDOW} of "
                    f"{target}:{anchor_line}; not found",
                )
    return CiteResult(doc_rel, doc_line, target, start, end, "ok", "")


def main():
    parser = argparse.ArgumentParser(
        description="Verify path:NNN citations in documentation."
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=DEFAULT_DOC_DIRS,
        help="Doc files or directories to scan (default: docs/).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    roots = [REPO_ROOT / p for p in args.paths]
    results: List[CiteResult] = []
    for doc in iter_docs(roots):
        doc_rel = doc.relative_to(REPO_ROOT).as_posix()
        for doc_line, target, start, end in extract_cites(doc):
            results.append(
                check_cite(doc_rel, doc_line, target, start, end)
            )

    failures = [r for r in results if r.status != "ok"]
    if args.verbose or failures:
        for r in results:
            if r.status == "ok" and not args.verbose:
                continue
            span = f"{r.line_start}" + (f"-{r.line_end}" if r.line_end else "")
            print(
                f"[{r.status}] {r.doc}:{r.doc_line}  ->  {r.target}:{span}"
                + (f"  ({r.detail})" if r.detail else "")
            )

    print(
        f"\nChecked {len(results)} citations across "
        f"{len(set(r.doc for r in results))} docs: "
        f"{len(results) - len(failures)} ok, {len(failures)} failing."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
