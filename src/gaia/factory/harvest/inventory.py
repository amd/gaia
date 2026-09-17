# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Everything the agent actually reaches for, enumerated and cross-tabbed.

The corpus contains 23,600 tool calls of which 14,705 are shell, spanning
~2,200 distinct binaries. Nothing enumerated them, so "what does an agent need
to be able to do" has only ever been answered from memory.

Three surfaces, kept apart because they are different integration problems:

* **agent tools** — what the harness itself exposes
* **MCP tools** — split by server, since each is a separate dependency
* **shell** — binary, then switches per binary, then the shape of the arguments

Each is cross-tabbed against the activity and domain from :mod:`tasks`, which is
the part that makes it actionable: "research needs these six binaries and
implementation needs these twenty" is a build decision, where a flat top-40 list
is trivia.

Shell parsing reuses ``report._binaries`` / ``report._segment_head``. Measured
against 14,705 real commands they resolve ``gh`` correctly out of
``"C:/Program Files/GitHub CLI/gh.exe"`` and leave only one visible noise token,
so they are extended here rather than replaced.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Tuple

from gaia.factory.harvest.report import _binaries, _segment_head
from gaia.factory.harvest.tasks import Task

#: A lone dash is stdin, not a switch.
_NOT_A_SWITCH = re.compile(r"^-+$")

#: ``head -20`` and ``tail -50`` are the same capability with a different count.
#: Collapsing the number keeps the idiom — which is what an inventory is for —
#: without turning every line count into its own row.
_NUMERIC_SHORTHAND = re.compile(r"^-(\d+)$")

#: Binaries whose arguments are data, not flags. ``echo "--- header ---"`` was
#: contributing ``---"`` as a switch, which is a quoted string fragment.
_ARGS_ARE_DATA = frozenset({"echo", "printf"})

#: Shell words that survive tokenisation but name no program. ``files`` comes
#: from an unquoted ``Program Files`` path; the rest are loop and test syntax
#: that ``_segment_head`` does not already drop.
_SHELL_NOISE = {"files", "program", "then", "fi", "done", "esac", "elif", "or", "and"}

#: Words lifted out of a ``python -c`` body or a quoted string. Shell parsing
#: cannot fully avoid reading into those — ``python -c "import x; print(y)"``
#: splits on the semicolon like any compound command — so the language keywords
#: that leak through are named rather than chased with more regex.
_LANGUAGE_NOISE = {
    "import",
    "print",
    "from",
    "def",
    "class",
    "return",
    "const",
    "let",
    "var",
    "function",
    "continue",
    "pass",
    "raise",
    "assert",
    "with",
    "as",
    "lambda",
    "true",
    "false",
    "null",
    "none",
    "passed",
    "failed",
    "fails",
    "pending",
    "bearer",
    "token",
    "balance",
    "no",
    "yes",
}

_NOISE_BINARIES = frozenset(_SHELL_NOISE | _LANGUAGE_NOISE)


#: Suffixes that mark a token as something a command *operates on*. At full
#: corpus scale these dominated the long tail — ``control.json``, ``drive.sh``,
#: ``instance.json`` were all being inventoried as though they were programs.
#: ``.sh`` is deliberately absent: a shell script genuinely is a program here.
_DATA_SUFFIXES = (
    ".json",
    ".md",
    ".mdx",
    ".txt",
    ".yml",
    ".yaml",
    ".html",
    ".css",
    ".log",
    ".csv",
    ".jsonl",
    ".toml",
    ".lock",
    ".xml",
)


def _looks_like_binary(name: str) -> bool:
    """Could this token plausibly name a program?

    Three rejections, each earned from the corpus rather than guessed:

    * single characters — loop variables and awk fields, never commands;
    * the noise list — words lifted out of a quoted string or script body;
    * **path and data-file tokens** — ``src`` alone was counted 2,168 times as a
      binary because it is the head of ``src/gaia/...``. A token carrying a path
      separator or a data suffix is what a command acted on, not the command.

    Anything else is allowed through: a genuine one-off tool is more interesting
    than a perfectly clean list.
    """
    if len(name) <= 1 or name in _NOISE_BINARIES:
        return False
    if "/" in name or "\\" in name or name.endswith(_DATA_SUFFIXES):
        return False
    # A bare directory name that only ever appears as a path head. Keeping this
    # narrow and explicit beats a heuristic that would also drop real tools.
    return name not in {"src", "tests", "docs", "hub", "tui", "util", "scripts"}


@dataclass
class ToolFacts:
    """One tool, however it is invoked."""

    name: str
    surface: str  # "agent" | "mcp" | "shell"
    calls: int = 0
    failures: int = 0
    server: str = ""  # MCP only
    switches: Counter = field(default_factory=Counter)  # shell only
    activities: Counter = field(default_factory=Counter)
    domains: Counter = field(default_factory=Counter)

    @property
    def failure_rate(self) -> float:
        return self.failures / self.calls if self.calls else 0.0


#: Flags whose value is an expression in another language. ``gh --jq '.[] |
#: fromdate | floor'`` splits on the pipes inside the filter, so ``fromdate`` and
#: ``floor`` were inventoried as programs. ``report._binaries`` already cuts
#: ``python -c`` bodies for the same reason; these are the same problem with a
#: different flag, cut here so the shared helper is left alone.
_FILTER_FLAGS = re.compile(r"\s(--jq|--template|--format|--filter|--expr)\b")


def _strip_filter_bodies(digest: str) -> str:
    """Drop everything after a flag whose value is a foreign expression."""
    m = _FILTER_FLAGS.search(digest)
    return digest[: m.start()] if m else digest


def _mcp_server(tool: str) -> str:
    """``mcp__playwright__browser_click`` -> ``playwright``."""
    parts = tool.split("__")
    return parts[1] if len(parts) >= 3 and parts[0] == "mcp" else ""


def _switches(digest: str) -> Iterable[Tuple[str, str]]:
    """Yield ``(binary, switch)`` for each flag in a compound command.

    Attributed to the binary that owns it: ``git log --oneline | head -20``
    yields ``(git, --oneline)`` and ``(head, -20)``, not four pairs against
    whichever program happened to come first.
    """
    cleaned = re.sub(r"`[^`]*`|\$\([^)]*\)", " ; ", _strip_filter_bodies(digest))
    for seg in re.split(r"&&|\|\||[;|)]|\n", cleaned):
        toks = seg.strip().split()
        if not toks:
            continue
        head = _segment_head(seg)
        if not head:
            continue
        name, start = head
        if not _looks_like_binary(name) or name in _ARGS_ARE_DATA:
            continue
        for tok in toks[start:]:
            # Stop at the first quote: everything after it is content, and
            # scanning into it invents switches out of prose. The quote can be
            # anywhere in the token, not just leading — `head -20"}` came from a
            # jq filter and was being recorded as a flag.
            if "'" in tok or '"' in tok:
                break
            if not tok.startswith("-") or _NOT_A_SWITCH.match(tok):
                continue
            numeric = _NUMERIC_SHORTHAND.match(tok)
            # `--flag=value` collapses to the flag; the value is per-invocation
            # noise that would explode the cardinality.
            flag = "-N" if numeric else tok.split("=", 1)[0]
            if len(flag) <= 24:
                yield name, flag


def build(tasks: Sequence[Task]) -> Dict[str, List[ToolFacts]]:
    """Inventory every surface, attributed to the tasks that used it."""
    agent: Dict[str, ToolFacts] = {}
    mcp: Dict[str, ToolFacts] = {}
    shell: Dict[str, ToolFacts] = {}

    for task in tasks:
        for step in task.steps:
            tool = step.get("tool", "unknown")
            failed = step.get("ok") is False
            server = _mcp_server(tool)
            table, surface = (mcp, "mcp") if server else (agent, "agent")
            rec = table.setdefault(tool, ToolFacts(tool, surface, server=server))
            rec.calls += 1
            rec.failures += failed
            rec.activities[task.activity] += 1
            rec.domains[task.domain] += 1

            if step.get("family") != "shell":
                continue
            digest = _strip_filter_bodies(step.get("arg_digest") or "")
            if not digest:
                continue
            for binary in _binaries(digest):
                if not _looks_like_binary(binary):
                    continue
                b = shell.setdefault(binary, ToolFacts(binary, "shell"))
                b.calls += 1
                b.failures += failed
                b.activities[task.activity] += 1
                b.domains[task.domain] += 1
            for binary, flag in _switches(digest):
                if binary in shell:
                    shell[binary].switches[flag] += 1

    order = lambda d: sorted(d.values(), key=lambda r: -r.calls)  # noqa: E731
    return {"agent": order(agent), "mcp": order(mcp), "shell": order(shell)}


#: A token has to recur across the corpus before it is treated as a real
#: program. Shell parsing cannot fully separate a command from the inside of a
#: quoted string or a ``python -c`` body, so the long tail is a mix of genuine
#: one-off tools and fragments like ``bearer`` or ``passed``. Requiring recurrence
#: is cheaper and more honest than trying to perfect the tokenizer.
MIN_CALLS_TO_COUNT = 10


def by_activity(
    inv: Dict[str, List[ToolFacts]], surface: str, min_calls: int = MIN_CALLS_TO_COUNT
) -> Dict[str, List[str]]:
    """Which tools each activity actually needs — the build-decision view.

    Two filters, and both are load-bearing. A tool must recur corpus-wide, or a
    fragment picked out of a quoted string dominates an activity that only has a
    handful of tasks. And the activity must account for a real share of that
    tool's use, so a single stray call does not make a binary look essential.
    """
    out: Dict[str, Counter] = defaultdict(Counter)
    for rec in inv.get(surface, []):
        if rec.calls < min_calls:
            continue
        for activity, n in rec.activities.items():
            if n >= 3 and n / rec.calls >= 0.15:
                out[activity][rec.name] = n
    return {a: [n for n, _ in c.most_common(12)] for a, c in out.items()}


def coverage(inv: Dict[str, List[ToolFacts]]) -> Dict[str, object]:
    """Headline counts, and the long tail that a top-N table always hides."""
    shell = inv.get("shell", [])
    total = sum(r.calls for r in shell)
    once = [r for r in shell if r.calls == 1]
    return {
        "agent_tools": len(inv.get("agent", [])),
        "mcp_tools": len(inv.get("mcp", [])),
        "mcp_servers": len({r.server for r in inv.get("mcp", []) if r.server}),
        "shell_binaries": len(shell),
        "shell_switch_pairs": sum(len(r.switches) for r in shell),
        "shell_calls": total,
        "binaries_used_once": len(once),
        "top10_share": (sum(r.calls for r in shell[:10]) / total if total else 0.0),
    }
