# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""No runtime string may tell a user to run the removed ``lemonade-server`` CLI.

Lemonade 10.7/10.8 removed ``lemonade-server serve`` and ``lemonade-server
pull``, so a hard-coded copy sends users to a command their machine does not
have (#4216). Start hints come from ``describe_start_hint()`` and pull hints
from ``describe_client_hint()``, which only name the legacy CLI when a legacy
install was actually resolved. The TUI resolves its own through the preflight
launcher.
"""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = ("src/gaia", "hub", "tui")
_SCAN_EXTENSIONS = {
    ".py",
    ".go",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".html",
    ".md",
}
_SKIP_DIRS = {"node_modules", "dist", "build", ".turbo", "tests", "test", "__tests__"}
_REMOVED_CLI = re.compile(r"lemonade-server\s+(serve|pull)\b")

# Files that may name the legacy CLI on purpose.
_ALLOWED = {
    # Builds the legacy argv only when a legacy install was resolved.
    "src/gaia/llm/lemonade_launcher.py",
    # Detects the stale hint from an older agent package and replaces it.
    "src/gaia/ui/_chat_helpers.py",
}

# Still name the removed CLI on main, with a fix in flight. Delete an entry
# when its fix lands; do NOT add to this set.
_PENDING = {
    # #3122
    "hub/agents/email/npm/SCORECARD.md",
    "hub/agents/email/npm/SKILL.md",
    "hub/agents/email/python/gaia_agent_email/api_routes.py",
    "hub/agents/email/python/gaia_agent_email/playground_html.py",
    "hub/agents/email/python/gaia_agent_email/query_routes.py",
    "hub/agents/email/python/packaging/gen_scorecard.py",
    "src/gaia/agents/base/readiness.py",
    "src/gaia/apps/webui/README.md",
    "src/gaia/llm/providers/lemonade.py",
    "tui/internal/ui/chat/modelcmd.go",
    # #4181 and #3122; stdio.py also names `lemonade-server pull <model>`.
    "hub/agents/gaia/npm/SCORECARD.md",
    "hub/agents/gaia/npm/SKILL.md",
    "hub/agents/gaia/python/gaia_agent/server.py",
    "hub/agents/gaia/python/gaia_agent/stdio.py",
    "hub/agents/gaia/python/packaging/gen_scorecard.py",
}


def _is_test_file(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith("_test.go")
        or ".test." in name
        or ".spec." in name
    )


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith(("#", "//", "*", "/*"))


def _runtime_files():
    for root in _SCAN_ROOTS:
        for path in sorted((_REPO / root).rglob("*")):
            if not path.is_file() or path.suffix not in _SCAN_EXTENSIONS:
                continue
            if any(part in _SKIP_DIRS for part in path.relative_to(_REPO).parts):
                continue
            # Changelogs record history, including what older releases said.
            if _is_test_file(path) or path.name == "CHANGELOG.md":
                continue
            yield path


def test_no_runtime_string_names_the_removed_lemonade_cli():
    offenders = []
    for path in _runtime_files():
        rel = path.relative_to(_REPO).as_posix()
        if rel in _ALLOWED or rel in _PENDING:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _REMOVED_CLI.search(line) and not _is_comment(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "These name `lemonade-server serve`/`pull`, which current Lemonade "
        "installs don't have. Use describe_start_hint() / describe_client_hint() "
        "from gaia.llm.lemonade_launcher (or the TUI preflight resolver):\n  "
        + "\n  ".join(offenders)
    )


def test_pending_entries_still_need_their_fix():
    """Once a pending file is fixed, its entry must go, or it hides a regression."""
    for rel in _PENDING:
        text = (_REPO / rel).read_text(encoding="utf-8")
        assert _REMOVED_CLI.search(
            text
        ), f"{rel} no longer names the removed CLI — remove it from _PENDING."
