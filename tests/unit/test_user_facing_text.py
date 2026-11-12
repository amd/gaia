# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Text the desktop app or the TUI can show must not send the user to a terminal.

A desktop install has no ``gaia`` on PATH and no ``pip``, so a reply that says
"run `gaia connectors connect google`" or "pip install ..." leaves the user
with nothing they can do (rc18: #7209). The same goes for features that are
gone: GAIA is one agent, so text must not offer an agent list, an agent
catalog, an Agent Hub or the standalone email agent (rc18: #7200).

The scan reads every string the agent, its tools and the Agent UI backend can
produce, the Agent UI's English strings, and the TUI's strings. Existing
violations are counted per file in ``KNOWN`` with the issue that tracks them;
matches that are not text for a user sit in ``NOT_USER_TEXT`` with the reason.
The count is exact: a new one fails, and so does fixing one without lowering
the number, so the list can only shrink.
"""

from __future__ import annotations

import argparse
import ast
import re
from collections import Counter
from pathlib import Path

from gaia.cli import build_parser

REPO = Path(__file__).resolve().parents[2]

#: Python the desktop app and TUI run: what it returns reaches the chat.
PYTHON_ROOTS = (
    "src/gaia/ui",
    "src/gaia/agents",
    "hub/agents/gaia/python/gaia_agent",
)
#: The Agent UI's own English text.
LOCALE = "src/gaia/apps/webui/src/i18n/locales/en.ts"
#: The TUI. It is a terminal, so a `gaia ...` command is something its user
#: can run; pip, dead commands and removed features are wrong there too.
TUI_ROOT = "tui"
TUI_RULES = ("pip", "dead command", "removed feature")


def _subcommands() -> list[str]:
    names: set[str] = set()
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            names |= set(action.choices)
    return sorted(names)


RULES = {
    # `gaia <any registered subcommand>`, written as something to type.
    "terminal command": re.compile(
        r"(?:^|[`'\"(]|\b[Rr]un:?\s+|\b[Ww]ith:?\s+)gaia\s+(?:-p\b|(?:"
        + "|".join(re.escape(name) for name in _subcommands())
        + r")\b)"
    ),
    "pip": re.compile(r"\bpip3?\s+install\b|\bpython3?\s+-m\s+(?:pip|playwright)\b"),
    # Lemonade 10.7 removed it; see CLAUDE.md.
    "dead command": re.compile(r"lemonade-server\s+serve"),
    "removed feature": re.compile(
        r"gaia agent list|agent catalog|agent hub|\bgaia hub\b|\bgaia email\b"
        r"|\bemail agent\b",
        re.IGNORECASE,
    ),
}

#: ``{file: {rule: (count, issue)}}`` for text that breaks a rule today.
KNOWN: dict[str, dict[str, tuple[int, str]]] = {
    "hub/agents/gaia/python/gaia_agent/chat_agent.py": {
        "pip": (1, "amd/gaia#7338"),
    },
    "hub/agents/gaia/python/gaia_agent/entry.py": {
        "pip": (3, "amd/gaia#7338"),
        "terminal command": (1, "amd/gaia#7338"),
    },
    "hub/agents/gaia/python/gaia_agent/stdio.py": {
        "terminal command": (1, "amd/gaia#7338"),
    },
    "hub/agents/gaia/python/gaia_agent/tool_bundles.py": {
        "removed feature": (1, "amd/gaia#7339"),
    },
    "src/gaia/agents/base/bootstrap.py": {
        "terminal command": (2, "amd/gaia#7338"),
    },
    "src/gaia/agents/base/memory.py": {
        "terminal command": (7, "amd/gaia#7338"),
    },
    "src/gaia/agents/base/readiness.py": {
        "terminal command": (3, "amd/gaia#7338"),
    },
    "src/gaia/agents/base/skill_deltas.py": {
        "terminal command": (2, "amd/gaia#7338"),
    },
    "src/gaia/agents/registry.py": {
        "terminal command": (1, "amd/gaia#7338"),
    },
    "src/gaia/agents/tools/browser_tools.py": {
        "terminal command": (1, "amd/gaia#7338"),
    },
    "src/gaia/agents/tools/document_text.py": {
        "pip": (2, "amd/gaia#7338"),
    },
    "src/gaia/agents/tools/email_tools.py": {
        "terminal command": (7, "amd/gaia#7338"),
    },
    "src/gaia/agents/tools/rag_tools.py": {
        "terminal command": (1, "amd/gaia#7338"),
    },
    "src/gaia/agents/tools/skill_learning_tools.py": {
        "terminal command": (3, "amd/gaia#7338"),
    },
    "src/gaia/agents/tools/skill_library_tools.py": {
        "terminal command": (2, "amd/gaia#7338"),
    },
    "src/gaia/ui/email_sidecar/profiles.py": {
        "removed feature": (6, "amd/gaia#4825"),
    },
    "src/gaia/ui/routers/memory.py": {
        "pip": (1, "amd/gaia#7338"),
    },
    "src/gaia/ui/routers/sessions.py": {
        "removed feature": (1, "amd/gaia#7339"),
        "terminal command": (1, "amd/gaia#7338"),
    },
    "src/gaia/ui/setup_runner.py": {
        "terminal command": (3, "amd/gaia#7338"),
    },
    "tui/internal/catalog/catalog.go": {
        "removed feature": (2, "amd/gaia#7339"),
    },
    "tui/internal/catalog/hub.go": {
        "removed feature": (2, "amd/gaia#7339"),
    },
    "tui/internal/client/negotiate.go": {
        "removed feature": (1, "amd/gaia#7339"),
    },
}

#: Matches that are not text for a user, each with why. Counted like KNOWN.
NOT_USER_TEXT: dict[str, dict[str, tuple[int, str]]] = {
    "hub/agents/gaia/python/gaia_agent/server.py": {
        "removed feature": (2, "API schema text for developers"),
    },
    "src/gaia/agents/base/console.py": {
        "pip": (1, "printed by a pip install that lacks rich, never by an app"),
    },
    "src/gaia/agents/base/tool_grants.py": {
        "pip": (2, "names commands GAIA holds for approval"),
    },
    "src/gaia/agents/tools/connect_card.py": {
        "terminal command": (
            1,
            "only for a caller outside both apps, already in a terminal",
        ),
    },
    "src/gaia/agents/tools/package_installs.py": {
        "pip": (1, "tells the agent to use the project's own environment"),
    },
    "src/gaia/ui/routers/chat.py": {
        "terminal command": (1, "returned only to the eval harness"),
    },
    "src/gaia/ui/security.py": {
        "terminal command": (1, "returned only to the eval harness"),
    },
}

_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _python_strings(path: Path):
    """Every string a module can produce: no docstrings, no log lines."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hidden: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            hidden.add(id(node.value))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOG_METHODS
        ):
            hidden.update(id(part) for part in ast.walk(node))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in hidden
        ):
            yield node.lineno, node.value


_GO_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_GO_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"|`([^`]*)`')


def _go_strings(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    # Blank comments out, keeping line numbers; a URL's // sits inside a string.
    code = _GO_STRING.sub(lambda m: m.group(0).replace("//", "\x00\x00"), text)
    code = _GO_COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), code)
    for match in _GO_STRING.finditer(code):
        literal = (match.group(1) or match.group(2) or "").replace("\x00\x00", "//")
        yield code.count("\n", 0, match.start()) + 1, literal


def _sources():
    """``(relative path, rules that apply, strings)`` for every scanned file."""
    for root in PYTHON_ROOTS:
        folder = REPO / root
        assert folder.is_dir(), f"{root} is gone; update PYTHON_ROOTS"
        for path in sorted(folder.rglob("*.py")):
            if "tests" in path.parts:
                continue
            yield path, tuple(RULES), _python_strings(path)
    locale = REPO / LOCALE
    assert locale.is_file(), f"{LOCALE} is gone; update LOCALE"
    lines = locale.read_text(encoding="utf-8").splitlines()
    yield locale, tuple(RULES), list(enumerate(lines, 1))
    tui = REPO / TUI_ROOT
    assert tui.is_dir(), f"{TUI_ROOT} is gone; update TUI_ROOT"
    for path in sorted(tui.rglob("*.go")):
        if path.name.endswith("_test.go") or "test" in path.relative_to(tui).parts:
            continue
        yield path, TUI_RULES, _go_strings(path)


def _violations() -> dict[tuple[str, str], list[str]]:
    found: dict[tuple[str, str], list[str]] = {}
    for path, rules, strings in _sources():
        relative = path.relative_to(REPO).as_posix()
        for line, text in strings:
            for rule in rules:
                match = RULES[rule].search(text)
                if match:
                    start = max(0, match.start() - 40)
                    excerpt = " ".join(text[start : match.end() + 40].split())
                    found.setdefault((relative, rule), []).append(
                        f"{relative}:{line}: {excerpt}"
                    )
    return found


VIOLATIONS = _violations()


def _allowed(path: str, rule: str) -> int:
    return (
        KNOWN.get(path, {}).get(rule, (0, ""))[0]
        + NOT_USER_TEXT.get(path, {}).get(rule, (0, ""))[0]
    )


def test_no_new_text_sends_the_user_to_a_terminal_or_a_removed_feature():
    new = []
    for (path, rule), lines in sorted(VIOLATIONS.items()):
        allowed = _allowed(path, rule)
        if len(lines) > allowed:
            new.append(
                f"[{rule}] {path}: {len(lines)} found, {allowed} known\n    "
                + "\n    ".join(lines)
            )
    assert not new, (
        "Text the desktop app or TUI can show breaks a rule. Say what the app "
        "does or where in Settings to go; do not name a command to type, and "
        "do not offer a feature that is gone.\n" + "\n".join(new)
    )


def test_a_fixed_violation_is_taken_off_the_list():
    stale = []
    for table in (KNOWN, NOT_USER_TEXT):
        for path, rules in sorted(table.items()):
            for rule, (_count, why) in sorted(rules.items()):
                found = len(VIOLATIONS.get((path, rule), []))
                if found < _allowed(path, rule):
                    stale.append(
                        f"{path} [{rule}]: {found} left, "
                        f"{_allowed(path, rule)} allowed ({why})"
                    )
    assert not stale, (
        "Fewer violations than KNOWN allows. Lower the count (or delete the "
        "entry at 0) so it cannot grow back:\n" + "\n".join(stale)
    )


def test_every_known_violation_names_its_issue():
    for table in (KNOWN, NOT_USER_TEXT):
        for path, rules in table.items():
            assert (REPO / path).is_file(), f"{path} is gone; delete its entry"
            for rule, (allowed, why) in rules.items():
                assert rule in RULES, f"{path}: unknown rule {rule!r}"
                assert allowed > 0, f"{path} [{rule}]: delete the entry instead of 0"
                assert why, f"{path} [{rule}]: say why"
    for path, rules in KNOWN.items():
        for rule, (_allowed_count, issue) in rules.items():
            assert re.fullmatch(r"amd/gaia#\d+", issue), f"{path} [{rule}]: {issue!r}"


def test_the_rules_catch_what_rc18_shipped():
    """The exact strings from the release test, so a rule cannot be loosened."""
    caught = Counter()
    for text in (
        "Connect an account first: Google: `gaia connectors connect google`",
        "No mailbox is connected. Run `gaia connectors connect google`.",
        "Install it with: pip install amd-gaia[browser] && "
        "python -m playwright install chromium",
        "Install one with: gaia agent install <id>",
        "Start it with: lemonade-server serve",
        "See `gaia agent list` for what is available to install.",
        "Browse the agent catalog to add one.",
        "The email agent is not installed.",
    ):
        hit = [rule for rule, pattern in RULES.items() if pattern.search(text)]
        assert hit, f"no rule catches: {text!r}"
        caught.update(hit)
    assert set(caught) == set(RULES)


def test_the_rules_leave_ordinary_text_alone():
    for text in (
        "The gaia daemon supervises the model server.",
        "GAIA agent sessions are kept for 30 days.",
        "Open Settings > Connectors and connect your Google account.",
        "It installs packages into the project's own environment.",
        "Send it as an email to the agent's owner.",
    ):
        hit = [rule for rule, pattern in RULES.items() if pattern.search(text)]
        assert not hit, f"{hit} wrongly catches: {text!r}"
