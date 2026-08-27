# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Render the markdown tables for a Claude Code corpus analysis.

Reads what ``scan`` wrote and emits tables with absolute counts *and* share of
total, so a figure can never be quoted without its denominator.  Optionally
joins a use-case label file (``<8-char-session-prefix> <primary> <secondary>``)
to break the corpus down by workflow.

Usage::

    python -m gaia.factory.harvest.report [--cache DIR] [--labels FILE]
"""

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from gaia.factory.harvest.scan import DEFAULT_OUT, percentile


def load_traces(cache: Path) -> List[dict]:
    path = cache / "traces.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} not found — run `gaia.factory.harvest.scan` first.")
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def all_steps(traces: List[dict]) -> List[dict]:
    """Every step in the corpus, subagents included.

    Subagents carry a large share of tool work.  Iterating only ``t["steps"]`` made
    every shell/binary table silently main-session-only while presenting
    itself as corpus-wide.
    """
    steps = []
    for t in traces:
        steps.extend(t["steps"])
        for sub in t.get("subagents", []):
            steps.extend(sub.get("steps_detail", []))
    return steps


def load_labels(path: Optional[Path]) -> Dict[str, str]:
    if not path:
        return {}
    if not path.exists():
        raise SystemExit(f"label file {path} not found")
    labels = {}
    for line in path.open(encoding="utf-8"):
        parts = line.split()
        if len(parts) >= 2:
            labels[parts[0]] = parts[1]
    return labels


def _pct(n: float, d: float) -> str:
    return f"{100 * n / d:.1f}%" if d else "—"


def corpus_table(traces: List[dict], stats: dict) -> str:
    tok = stats["tokens"]
    out = ["| Measure | Value | Share |", "|---|---:|---:|"]
    rows = [
        ("Sessions", f"{stats['sessions']:,}", "—"),
        (
            "Subagent transcripts",
            f"{stats['traces_including_subagents'] - stats['sessions']:,}",
            "—",
        ),
        (
            "Tool calls — main sessions",
            f"{stats['tool_calls_main']:,}",
            _pct(stats["tool_calls_main"], stats["total_tool_calls"]),
        ),
        (
            "Tool calls — subagents",
            f"{stats['tool_calls_subagent']:,}",
            _pct(stats["tool_calls_subagent"], stats["total_tool_calls"]),
        ),
        ("Tool calls — total", f"{stats['total_tool_calls']:,}", "100%"),
        (
            "Model calls — main sessions",
            f"{stats['model_calls_main']:,}",
            _pct(stats["model_calls_main"], stats["model_calls"]),
        ),
        (
            "Model calls — subagents",
            f"{stats['model_calls_subagent']:,}",
            _pct(stats["model_calls_subagent"], stats["model_calls"]),
        ),
        ("Model calls — total", f"{stats['model_calls']:,}", "100%"),
        (
            "Tool calls per model call",
            f"{stats['tool_calls_per_model_call']}",
            "—",
        ),
        (
            "Failed tool calls",
            f"{stats['failed_tool_calls']:,}",
            _pct(stats["failed_tool_calls"], stats["resolved_tool_calls"]),
        ),
        (
            "Tokens — input (uncached)",
            f"{tok['input']:,}",
            _pct(tok["input"], tok["total"]),
        ),
        ("Tokens — output", f"{tok['output']:,}", _pct(tok["output"], tok["total"])),
        (
            "Tokens — cache write",
            f"{tok['cache_write']:,}",
            _pct(tok["cache_write"], tok["total"]),
        ),
        (
            "Tokens — cache read",
            f"{tok['cache_read']:,}",
            _pct(tok["cache_read"], tok["total"]),
        ),
        ("Tokens — total", f"{tok['total']:,}", "100%"),
    ]
    for name, val, share in rows:
        out.append(f"| {name} | {val} | {share} |")
    return "\n".join(out)


def family_table(stats: dict) -> str:
    fam = stats["families"]
    total = sum(fam.values())
    out = ["| Tool family | Calls | Share |", "|---|---:|---:|"]
    for k, v in fam.items():
        out.append(f"| {k} | {v:,} | {_pct(v, total)} |")
    out.append(f"| **total** | **{total:,}** | **100%** |")
    return "\n".join(out)


def usecase_table(traces: List[dict], labels: Dict[str, str]) -> str:
    if not labels:
        return "_no label file supplied_"
    by: Dict[str, List[dict]] = defaultdict(list)
    for t in traces:
        tag = labels.get(t["session_id"][:8])
        if tag:
            by[tag].append(t)

    def calls_of(t: dict) -> int:
        return t["total_calls"] + sum(
            s.get("total_calls", s["steps"]) for s in t["subagents"]
        )

    tot_sessions = sum(len(v) for v in by.values())
    tot_calls = sum(calls_of(t) for v in by.values() for t in v)

    rows = []
    for tag, group in by.items():
        calls = [calls_of(t) for t in group]
        total = sum(calls)
        # Every column on this row must share one scope, or the table
        # contradicts its own glossary.
        resolved = sum(
            t["attempt_count"] + sum(s["attempt_count"] for s in t["subagents"])
            for t in group
        )
        succeeded = sum(
            t["success_count"] + sum(s["success_count"] for s in t["subagents"])
            for t in group
        )
        failed = resolved - succeeded
        deleg = sum(1 for t in group if t["subagents"])
        toks = sum(
            t["usage"]["total"]
            + sum(s["usage"].get("total", 0) for s in t["subagents"])
            for t in group
        )
        mcalls = sum(
            t["assistant_turns"]
            + sum(s.get("assistant_turns", 0) for s in t["subagents"])
            for t in group
        )
        turns = sum(len(t["prompts"]) for t in group)
        subs = sum(len(t["subagents"]) for t in group)
        rows.append(
            {
                "tag": tag,
                "n": len(group),
                "calls": total,
                "mcalls": mcalls,
                "turns": turns,
                "subs": subs,
                "med": int(statistics.median(calls)),
                "p90": int(percentile(calls, 90)),
                "fail": 100 * failed / resolved if resolved else 0,
                "deleg": 100 * deleg / len(group),
                "tokens": toks,
            }
        )
    rows.sort(key=lambda r: -r["calls"])

    out = [
        "| Use-case | Sessions | % of sessions | Tool Calls | % of calls | "
        "Model Calls | User turns | Subagents | Med | p90 | Fail% | Deleg% | Tokens |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        out.append(
            f"| {r['tag']} | {r['n']} | {_pct(r['n'], tot_sessions)} | {r['calls']:,} | "
            f"{_pct(r['calls'], tot_calls)} | {r['mcalls']:,} | {r['turns']:,} | "
            f"{r['subs']} | {r['med']} | {r['p90']} | "
            f"{r['fail']:.1f}% | {r['deleg']:.0f}% | {r['tokens']/1e6:.0f}M |"
        )
    out.append(
        f"| **total** | **{tot_sessions}** | **100%** | **{tot_calls:,}** | **100%** | "
        f"**{sum(r['mcalls'] for r in rows):,}** | "
        f"**{sum(r['turns'] for r in rows):,}** | "
        f"**{sum(r['subs'] for r in rows)}** | | | | | |"
    )
    out += [
        "",
        "**Column definitions** — see Appendix D for every term.",
        "",
        "| Column | Definition |",
        "|---|---|",
        "| **Sessions** | Top-level Claude Code sessions classified into this use-case. |",
        "| **Tool Calls** | Tool invocations, subagents included. |",
        "| **Model Calls** | Distinct API responses (one `message.id`), subagents included. |",
        "| **User turns** | Human messages, summed across the use-case. Harness-injected turns excluded. |",
        "| **Subagents** | Subagent runs spawned, summed across the use-case. |",
        "| **Med / p90** | Median and 90th-percentile *tool calls per session* within this use-case. A wide gap means one runaway session. |",
        "| **Fail%** | Share of resolved tool calls that returned an error. Tool-level, not task-level. |",
        "| **Deleg%** | Share of this use-case's sessions that spawned at least one subagent. |",
        "| **Tokens** | All four token classes summed, session and its subagents. |",
    ]
    return "\n".join(out)


def models_table(stats: dict) -> str:
    """Model usage by token share — the denominator people actually want.

    Counting traces double-counts any trace that switched model mid-run, which
    is why a naive share column summed above 100%.
    """
    tbm = stats.get("tokens_by_model", {})
    total = sum(tbm.values())
    pairs = stats.get("model_trace_pairs", 0)
    out = [
        f"_{pairs:,} trace-model pairs across {stats['traces_including_subagents']:,} "
        "traces — a trace that switches model counts under both, so trace counts "
        "sum above the corpus. Token share is the unambiguous measure._\n",
        "| Model | Traces | Tokens | % of tokens |",
        "|---|---:|---:|---:|",
    ]
    for m, tok in tbm.items():
        out.append(
            f"| `{m}` | {stats['models'].get(m, 0):,} | {tok/1e9:.2f}B | {_pct(tok, total)} |"
        )
    out.append(f"| **total** | **{pairs:,}** | **{total/1e9:.2f}B** | **100%** |")
    return "\n".join(out)


def error_table(stats: dict) -> str:
    errs = stats["effectiveness"]["error_classes"]
    total = sum(errs.values())
    out = [
        "| Failure class | Count | % of failures | % of all calls |",
        "|---|---:|---:|---:|",
    ]
    all_calls = stats["total_tool_calls"]
    for k, v in errs.items():
        out.append(f"| `{k}` | {v} | {_pct(v, total)} | {_pct(v, all_calls)} |")
    out.append(f"| **total** | **{total}** | **100%** | {_pct(total, all_calls)} |")
    return "\n".join(out)


_SKIP_TOKENS = {
    "sudo",
    "command",
    "time",
    "nohup",
    "exec",
    "env",
    "then",
    "do",
    "else",
    "elif",
    "fi",
    "done",
    "esac",
    "in",
    "for",
    "if",
    "while",
    "case",
    "function",
    "return",
    "!",
}

# The same tool reached by different spellings. `github` is what survives
# splitting the Windows path "/c/Program Files/GitHub CLI/gh.exe" on "/".
_BINARY_ALIASES = {
    "github": "gh",
    "python3": "python",
    "py": "python",
    "rg": "grep",
    "ripgrep": "grep",
}


def _segment_head(seg: str) -> Optional[tuple]:
    """The binary a single shell segment invokes, and where its args start.

    Returns ``(name, index_after_head)`` or ``None``.  ``flags_table`` and
    ``_binaries`` must agree on this or the two tables disagree about which
    binary ran: a quoted path like ``"/c/Program Files/.../gh.exe"`` splits on
    whitespace and its first token reads as ``program``.
    """

    toks = seg.strip().split()
    for i, tok in enumerate(toks):
        if "=" in tok and not tok.startswith("-") and not tok.startswith("/"):
            continue  # VAR=value prefix
        if tok.startswith(("-", "(", "{", "'", '"', "$")):
            continue
        name = tok.split("/")[-1].strip("\"'()").lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if not name or not re.match(r"^[a-z_][a-z0-9_.+-]*$", name):
            return None
        if name in _SKIP_TOKENS:
            continue
        return _BINARY_ALIASES.get(name, name), i + 1
    return None


def _binaries(cmd: str) -> List[str]:
    """Every binary a compound shell command invokes.

    Splits on shell operators and takes the head of each segment, skipping
    leading ``VAR=value`` assignments and wrappers like ``sudo``.  Substitutions
    (``$(...)``) are counted too — they run a real process.
    """

    found = []
    # An inline script body (`python -c "..."`, a heredoc) is data, not a
    # command list; parsing past it invents binaries out of the source text.
    m = re.search(r"(?:python3?|py|node|perl|ruby|bash|sh|zsh)\s+-[ce]\s|<<", cmd)
    if m:
        cmd = cmd[: m.end()]
    cleaned = cmd.replace("$(", " ; ").replace("`", " ; ")
    for seg in re.split(r"&&|\|\||[;|)]|\n", cleaned):
        head = _segment_head(seg)
        if head:
            found.append(head[0])
    return found


def binary_table(traces: List[dict], top: int = 40) -> str:
    """Frequency of every binary invoked through the shell."""
    cmds = [
        s["arg_digest"]
        for s in all_steps(traces)
        if s["family"] == "shell" and s["arg_digest"]
    ]
    counts: Counter = Counter()
    for c in cmds:
        counts.update(_binaries(c))
    total = sum(counts.values()) or 1
    # Two thirds of "distinct binaries" are English words scraped out of
    # heredocs and echo strings, seen exactly once. Report only what recurs.
    recurring = {k: v for k, v in counts.items() if v >= 10}
    out = [
        f"_{total:,} invocations in {len(cmds):,} shell commands. "
        f"{len(recurring):,} distinct binaries recur (10+ times), covering "
        f"{sum(recurring.values()):,} invocations "
        f"({100 * sum(recurring.values()) / total:.1f}%). The single-sighting "
        f"tail is parsing noise from inline scripts and is not a measurement._\n",
        "| # | Binary | Invocations | % of invocations | Cumulative |",
        "|---:|---|---:|---:|---:|",
    ]
    cum = 0
    for i, (name, v) in enumerate(counts.most_common(top), 1):
        cum += v
        out.append(
            f"| {i} | `{name}` | {v:,} | {_pct(v, total)} | {_pct(cum, total)} |"
        )
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--labels", type=Path, default=None)
    args = ap.parse_args()

    traces = load_traces(args.cache)
    stats = json.loads((args.cache / "stats.json").read_text(encoding="utf-8"))
    labels = load_labels(args.labels)

    print("## Corpus\n")
    print(corpus_table(traces, stats))
    print("\n## Tool families\n")
    print(family_table(stats))
    print("\n## Binary frequency\n")
    print(binary_table(traces))
    print("\n## Shell switches\n")
    print(flags_table(traces))
    print("\n## Shell composition patterns\n")
    print(patterns_table(traces))
    print("\n## Shell output shape\n")
    print(output_table(traces))
    print("\n## Code-work profile\n")
    print(codework_table(traces))
    print("\n## Cost and pricing assumptions\n")
    print(period_line(traces))
    print()
    print(pricing_table(stats))
    print("\n## Models\n")
    print(models_table(stats))
    print("\n## Failures\n")
    print(error_table(stats))
    print("\n## Use-cases\n")
    print(usecase_table(traces, labels))
    print("\n## Token economics by use-case\n")
    print(token_table(traces, labels))
    print("\n## Tokens and cost — main agent vs subagents\n")
    print(period_line(traces))
    print()
    print(scope_token_table(traces))
    print("\n### Subagent work by category\n")
    print(subagent_category_table(traces))
    print("\n## Friction by use-case\n")
    print(friction_table(traces, labels))
    print("\n## Session size and duration\n")
    print(distribution_table(stats))
    print("\n## Failure detail\n")
    print(failure_by_tool_table(stats))
    print("\n## Delegation detail\n")
    print(delegation_table(stats))
    print("\n## Appendix A — how an agentic coding session works\n")
    print(agent_loop_primer(stats, traces))
    print("\n## Appendix B — how prompt caching works\n")
    print(caching_explainer(stats))
    print("\n## Appendix C — methodology and how to read these numbers\n")
    print(methodology_primer(stats, traces))
    print("\n## Appendix D — glossary of terms\n")
    print(glossary_appendix(stats))


def flags_table(traces: List[dict], top_bins: int = 12, top_flags: int = 6) -> str:
    """Which switches each major binary was actually invoked with.

    Flag choice reveals intent that the binary name alone hides — whether a
    search was scoped, whether output was truncated, whether a read was paged.
    """

    per_bin: Dict[str, Counter] = defaultdict(Counter)
    bin_calls: Counter = Counter()
    for s in all_steps(traces):
        if s["family"] != "shell" or not s["arg_digest"]:
            continue
        cmd = s["arg_digest"]
        for seg in re.split(r"&&|\|\||[;|]|\n", cmd):
            parsed = _segment_head(seg)
            if not parsed:
                continue
            head, start = parsed
            bin_calls[head] += 1
            quote = ""
            for tok in seg.strip().split()[start:]:
                # A quoted body is data, not flags — `echo "--short"` is text.
                if quote:
                    if tok.endswith(quote):
                        quote = ""
                    continue
                if tok[:1] in ("'", '"') and not tok.endswith(tok[0]):
                    quote = tok[0]
                    continue
                if tok.endswith((')"', ")", '"', "'")):
                    continue
                if tok.startswith("--"):
                    per_bin[head][tok.split("=")[0]] += 1
                elif re.match(r"^-[A-Za-z]+$", tok):
                    # Bundled short flags (-rn) are separate switches.
                    for ch in tok[1:]:
                        per_bin[head][f"-{ch}"] += 1
    out = [
        "| Binary | Segments | Most-used switches |",
        "|---|---:|---|",
    ]
    for name, n in bin_calls.most_common(top_bins):
        flags = per_bin.get(name)
        if not flags:
            continue
        shown = " · ".join(f"`{f}` {c:,}" for f, c in flags.most_common(top_flags))
        out.append(f"| `{name}` | {n:,} | {shown} |")
    return "\n".join(out)


def patterns_table(traces: List[dict]) -> str:
    """Shell composition idioms — how commands are built, not just which."""

    cmds = [
        s["arg_digest"]
        for s in all_steps(traces)
        if s["family"] == "shell" and s["arg_digest"]
    ]
    n = len(cmds) or 1
    checks = [
        ("compound (`&&` / `;`)", r"&&|;"),
        ("pipes into another command", r"(?<!\|)\|(?!\|)"),
        ("output truncated (`head`/`tail`)", r"\b(head|tail)\b"),
        ("redirects to a file (`>`)", r"(?<![0-9])>{1,2}(?!&)"),
        ("suppresses stderr (`2>`)", r"2>"),
        ("command substitution `$(...)`", r"\$\("),
        ("inline script (`-c`, heredoc)", r"\b(python3?|node|perl|bash|sh)\s+-c|<<"),
        ("quotes a path with spaces", r"\"[^\"]*/[^\"]* [^\"]*\""),
        ("a `for` / `while` loop", r"\b(for|while)\s+\w+\s+in\b|\bwhile\s+\["),
        (r"conditional (`if`, `\|\|`)", r"\bif\s+|\|\|"),
        ("`grep` chained after another", r"\|\s*(grep|rg)\b"),
        ("counts results (`wc -l`)", r"\bwc\s+-l\b"),
        ("sleeps / polls", r"\bsleep\b"),
        ("targets a git worktree path", r"worktree"),
    ]
    out = ["| Shell idiom | Commands | % of shell commands |", "|---|---:|---:|"]
    for label, pat in checks:
        c = sum(1 for x in cmds if re.search(pat, x))
        out.append(f"| {label} | {c:,} | {_pct(c, n)} |")
    out.append(f"| **shell commands total** | **{n:,}** | **100%** |")
    return "\n".join(out)


def output_table(traces: List[dict], top: int = 14) -> str:
    """What each shell binary actually returned — size, emptiness, failure.

    Output shape is the missing half of "what did the agent do": a command
    whose result is empty answered a question, a command returning 40K of text
    spent context, and a command that failed cost a round trip.
    """

    by_bin: Dict[str, List[dict]] = defaultdict(list)
    for s in all_steps(traces):
        if s["family"] != "shell" or not s["arg_digest"]:
            continue
        for b in _binaries(s["arg_digest"])[:1]:  # the leading binary
            by_bin[b].append(s)

    rows = []
    for name, steps in by_bin.items():
        if len(steps) < 100:
            continue
        chars = [s["result_chars"] for s in steps]
        resolved = [s for s in steps if s["ok"] is not None]
        failed = sum(1 for s in resolved if not s["ok"])
        empty = sum(1 for s in steps if s["result_chars"] < 20)
        huge = sum(1 for s in steps if s["result_chars"] > 10000)
        rows.append(
            {
                "bin": name,
                "n": len(steps),
                "med": int(statistics.median(chars)) if chars else 0,
                "p90": int(percentile(chars, 90)),
                "empty": 100 * empty / len(steps),
                "huge": 100 * huge / len(steps),
                "fail": 100 * failed / len(resolved) if resolved else 0.0,
            }
        )
    rows.sort(key=lambda r: -r["n"])
    out = [
        "_Leading binary of each command. Median/p90 are result size in "
        "characters; `empty` means the command answered with (almost) nothing._\n",
        "| Binary | Commands | Median out | p90 out | Empty | >10K | Fail% |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows[:top]:
        out.append(
            f"| `{r['bin']}` | {r['n']:,} | {r['med']:,} | {r['p90']:,} | "
            f"{r['empty']:.0f}% | {r['huge']:.0f}% | {r['fail']:.1f}% |"
        )
    return "\n".join(out)


def codework_table(traces: List[dict]) -> str:
    """How the agent actually edits, searches and verifies code.

    The tool-family view says *what* was called; this says how each act of
    code work was shaped — how big an edit was, how a file was read, what a
    search returned, and how often a verification command failed.
    """

    steps = all_steps(traces)
    lines: List[str] = []

    edits = [s for s in steps if s["family"] in ("edit", "write")]
    reads = [s for s in steps if s["family"] == "read"]
    searches = [s for s in steps if s["family"] == "search"]

    def _stat(rows, key="result_chars"):
        vals = [r[key] for r in rows]
        return (
            int(statistics.median(vals)) if vals else 0,
            int(percentile(vals, 90)) if vals else 0,
        )

    # How concentrated is editing? A few files taking most edits means the
    # work is deep, not broad.
    edit_targets: Counter = Counter(s["arg_digest"] for s in edits if s["arg_digest"])
    top_share = sum(
        c for _, c in edit_targets.most_common(int(len(edit_targets) * 0.1) or 1)
    ) / max(sum(edit_targets.values()), 1)
    read_targets: Counter = Counter(s["arg_digest"] for s in reads if s["arg_digest"])
    reread = sum(c - 1 for c in read_targets.values() if c > 1)

    empty_search = sum(1 for s in searches if s["result_chars"] < 20)
    med_r, p90_r = _stat(reads)
    med_s, p90_s = _stat(searches)

    lines += [
        "| Measure | Value | Reading |",
        "|---|---:|---|",
        (
            f"| Edit + write calls | {len(edits):,} | "
            f"{_pct(len(edits), len(steps))} of all tool calls |"
        ),
        (
            f"| Distinct files edited | {len(edit_targets):,} | "
            f"{sum(edit_targets.values()) / max(len(edit_targets), 1):.1f}"
            " edits per file |"
        ),
        (
            f"| Edits landing in the top 10% of files | {top_share*100:.0f}% | "
            "work is concentrated, not spread |"
        ),
        (
            f"| Files read more than once | "
            f"{sum(1 for c in read_targets.values() if c > 1):,} | "
            f"{reread:,} re-reads ({_pct(reread, len(reads))} of reads) |"
        ),
        (
            f"| Median / p90 read result | {med_r:,} / {p90_r:,} chars | "
            "how much context one file costs |"
        ),
        (
            f"| Median / p90 search result | {med_s:,} / {p90_s:,} chars | "
            "searches return little; they are probes |"
        ),
        (
            f"| Searches returning nothing | {empty_search:,} | "
            f"{_pct(empty_search, len(searches))} of searches were a miss |"
        ),
    ]

    # Verification: which commands are run to check work, and how often they fail.
    verify = {
        "pytest / test runner": r"\b(pytest|npm test|vitest|jest|go test|ctest|bats)\b",
        "lint / format": r"\b(black|isort|ruff|flake8|eslint|prettier|mypy|lint\.py)\b",
        "build / compile": r"\b(cmake|make|msbuild|tsc|cargo|gcc|g\+\+|go build)\b",
        "git diff / status": r"\bgit\s+(diff|status)\b",
        "gh pr checks": r"\bgh\s+pr\s+(checks|view)\b",
    }

    lines += [
        "",
        "| Verification command | Runs | % of shell | Fail% |",
        "|---|---:|---:|---:|",
    ]
    shell = [s for s in steps if s["family"] == "shell" and s["arg_digest"]]
    for label, pat in verify.items():
        rx = re.compile(pat, re.I)
        hits = [s for s in shell if rx.search(s["arg_digest"])]
        resolved = [s for s in hits if s["ok"] is not None]
        fail = sum(1 for s in resolved if not s["ok"])
        lines.append(
            f"| {label} | {len(hits):,} | {_pct(len(hits), len(shell))} | "
            f"{100*fail/len(resolved) if resolved else 0:.1f}% |"
        )
    return "\n".join(lines)


def token_table(traces: List[dict], labels: Dict[str, str]) -> str:
    """Token composition and cost per use-case.

    Cost is meaningless without a period, so the span is printed inline.

    A single "tokens" total hides the thing that matters: what share is cheap
    cache-read versus expensive fresh input and output.
    """

    if not labels:
        return "_no label file supplied_"
    by: Dict[str, List[dict]] = defaultdict(list)
    for t in traces:
        tag = labels.get(t["session_id"][:8])
        if tag:
            by[tag].append(t)

    from gaia.factory.harvest.scan import price_for

    def usage_of(t: dict) -> Dict[str, int]:
        acc = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        for u in [t["usage"]] + [s["usage"] for s in t["subagents"]]:
            acc["input"] += u.get("input_tokens", 0)
            acc["output"] += u.get("output_tokens", 0)
            acc["cache_read"] += u.get("cache_read_tokens", 0)
            acc["cache_write"] += u.get("cache_write_tokens", 0)
        return acc

    def cost_of(t: dict) -> tuple[float, float]:
        """(fresh, cache) USD, priced per model that produced the tokens."""
        fresh = cache = 0.0
        by_model = [t.get("usage_by_model", {})] + [
            s.get("usage_by_model", {}) for s in t["subagents"]
        ]
        for group in by_model:
            for model, u in group.items():
                r = price_for(model)
                if r is None:
                    continue
                fresh += (
                    u.get("input_tokens", 0) * r["input"]
                    + u.get("output_tokens", 0) * r["output"]
                ) / 1_000_000
                cache += (
                    u.get("cache_write_tokens", 0) * r["write"]
                    + u.get("cache_read_tokens", 0) * r["read"]
                ) / 1_000_000
        return fresh, cache

    rows = []
    for tag, group in by.items():
        agg = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        totals = []
        fresh_usd = cache_usd = 0.0
        for t in group:
            u = usage_of(t)
            for k in agg:
                agg[k] += u[k]
            totals.append(sum(u.values()))
            f, c = cost_of(t)
            fresh_usd += f
            cache_usd += c
        tot = sum(agg.values()) or 1
        rows.append(
            {
                "tag": tag,
                "n": len(group),
                "tot": tot,
                "agg": agg,
                "min": min(totals),
                "med": int(statistics.median(totals)),
                "max": max(totals),
                "fresh_usd": fresh_usd,
                "cache_usd": cache_usd,
            }
        )
    rows.sort(key=lambda r: -r["tot"])
    grand = sum(r["tot"] for r in rows) or 1

    out = [
        period_line(traces),
        "",
        "| Use-case | Sessions | Total tokens | % of corpus | Cache-read | "
        "Cache-write | Output | Input (uncached) | Min | Median | Max | "
        "Fresh $ | Cache $ | Total $ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        a, tot = r["agg"], r["tot"]
        out.append(
            f"| {r['tag']} | {r['n']} | {tot/1e6:,.0f}M | {_pct(tot, grand)} | "
            f"{_pct(a['cache_read'], tot)} | {_pct(a['cache_write'], tot)} | "
            f"{_pct(a['output'], tot)} | {_pct(a['input'], tot)} | "
            f"{r['min']/1e6:.1f}M | {r['med']/1e6:.1f}M | {r['max']/1e6:,.0f}M | "
            f"${r['fresh_usd']:,.0f} | ${r['cache_usd']:,.0f} | "
            f"${r['fresh_usd'] + r['cache_usd']:,.0f} |"
        )
    out.append(
        f"| **total** | | **{grand/1e6:,.0f}M** | **100%** | | | | | | | | "
        f"**${sum(r['fresh_usd'] for r in rows):,.0f}** | "
        f"**${sum(r['cache_usd'] for r in rows):,.0f}** | "
        f"**${sum(r['fresh_usd'] + r['cache_usd'] for r in rows):,.0f}** |"
    )
    return "\n".join(out)


def friction_table(traces: List[dict], labels: Dict[str, str]) -> str:
    """Human-interaction friction per use-case.

    Previously computed by an ad-hoc script, which meant it vanished from the
    report the moment the script was not re-run. Generated here so it is part
    of the reproducible output.
    """

    from gaia.factory.harvest.analyze import _CORRECTION_RE, duration_minutes
    from gaia.factory.harvest.reader import Trace

    if not labels:
        return "_no label file supplied_"
    by: Dict[str, List[dict]] = defaultdict(list)
    for t in traces:
        tag = labels.get(t["session_id"][:8])
        if tag:
            by[tag].append(t)

    rows = []
    for tag, group in by.items():
        corr = sum(
            1 for t in group if any(_CORRECTION_RE.search(p) for p in t["prompts"][1:])
        )
        intr = sum(
            1
            for t in group
            if t["interrupts"] or any(s.get("interrupts") for s in t["subagents"])
        )
        turns = [len(t["prompts"]) for t in group]
        durs = []
        for t in group:
            d = duration_minutes(
                Trace(
                    session_id=t["session_id"],
                    project=t["project"],
                    started_at=t["started_at"],
                    last_at=t["last_at"],
                )
            )
            if d is not None:
                durs.append(d)
        calls = sum(
            t["total_calls"]
            + sum(s.get("total_calls", s["steps"]) for s in t["subagents"])
            for t in group
        )
        rows.append(
            {
                "tag": tag,
                "n": len(group),
                "corr": 100 * corr / len(group),
                "intr": 100 * intr / len(group),
                "turns": int(statistics.median(turns)),
                "dur": statistics.median(durs) if durs else 0.0,
                "calls": calls,
            }
        )
    rows.sort(key=lambda r: -r["intr"])
    out = [
        "| Use-case | Sessions | Corr% | Intr% | Median turns | Median minutes |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        out.append(
            f"| {r['tag']} | {r['n']} | {r['corr']:.0f}% | {r['intr']:.0f}% | "
            f"{r['turns']} | {r['dur']:.0f} |"
        )
    return "\n".join(out)


def distribution_table(stats: dict) -> str:
    """Session size and duration distribution."""
    b = stats["step_buckets"]
    total = sum(b.values()) or 1
    order = ["0 (conversation only)", "1-5", "6-20", "21-50", "51-100", "100+"]
    out = [
        "_Main-session tool calls only (subagent calls excluded), so these "
        "buckets are not the corpus-wide Tool Calls figure._\n",
        "| Tool calls in session (main) | Sessions | Share |",
        "|---|---:|---:|",
    ]
    for k in order:
        if k in b:
            out.append(f"| {k} | {b[k]} | {_pct(b[k], total)} |")
    out.append(f"| **total** | **{total}** | **100%** |")

    s = stats["steps_per_session"]
    t = stats["turns_per_session"]
    d = stats["effectiveness"]["duration_minutes"]
    out += [
        "",
        "| Per session (main) | p50 | p75 | p90 | max |",
        "|---|---:|---:|---:|---:|",
        f"| Tool calls | {s['p50']} | {s['p75']} | {s['p90']} | {s['max']} |",
        f"| User turns | {t['p50']} | — | {t['p90']} | {t['max']} |",
        f"| Duration (min, elapsed) | {d['p50']} | {d['p75']} | {d['p90']} | — |",
        "",
        "_Duration is elapsed wall-clock, not time worked: p90 of "
        f"{d['p90']:.0f} min is a session left open, not one being used. Only "
        "the median is meaningful._",
    ]
    return "\n".join(out)


def failure_by_tool_table(stats: dict) -> str:
    """Full per-tool failure rates, and per-family."""
    prof = stats["effectiveness"]["error_profile"]
    out = [
        "_Tools called at least 50 times, top 15 by failure rate — a rate on a "
        "handful of calls is noise. The family table below is complete._\n",
        "| Tool | Calls | Failures | Rate |",
        "|---|---:|---:|---:|",
    ]
    for r in prof["by_tool"]:
        out.append(
            f"| `{r['tool']}` | {r['calls']:,} | {r['failures']:,} | {r['rate_pct']}% |"
        )
    out += ["", "| Tool family | Calls | Failures | Rate |", "|---|---:|---:|---:|"]
    for fam, v in prof["by_family"].items():
        out.append(f"| {fam} | {v['calls']:,} | {v['failures']:,} | {v['rate_pct']}% |")
    mvs = prof["main_vs_subagent"]
    dec = prof["rate_by_session_decile_pct"]
    after = prof["after_a_failure"]
    st = prof["streaks"]
    out += [
        "",
        "| Recovery / distribution | Value |",
        "|---|---:|",
        f"| Main-session failure rate | {mvs['main_rate_pct']}% ({mvs['main_failures']:,}) |",
        f"| Subagent failure rate | {mvs['subagent_rate_pct']}% ({mvs['subagent_failures']:,}) |",
        f"| Failure rate by session decile | {' · '.join(f'{x}%' for x in dec)} |",
        f"| Failures with a following call | {after['failures_with_a_next_call']:,} |",
        f"| Next call succeeded | {after['next_call_succeeded_pct']}% |",
        f"| Next call blindly retried identical | {after['retried_identical_call_pct']}% |",
        f"| Failure streaks (1 / 2 / 3 / 4 / 5+) | "
        f"{st.get('len_1', 0):,} · {st.get('len_2', 0)} · {st.get('len_3', 0)} · "
        f"{st.get('len_4', 0)} · {st.get('len_5plus', 0)} |",
    ]
    return "\n".join(out)


def delegation_table(stats: dict) -> str:
    d = stats["effectiveness"]["delegation"]
    out = [
        "| Delegation | Value |",
        "|---|---:|",
        f"| Sessions that delegated | {d['sessions_delegating']} ({d['pct_sessions']}%) |",
        f"| Subagent transcripts | {d['subagent_transcripts']:,} |",
        f"| Subagents per delegating session | {d['avg_subagents_per_delegating_session']} |",
        f"| Subagent tool calls | {d['subagent_tool_calls']:,} |",
        "",
        "| Subagent tool | Calls | Share of subagent work |",
        "|---|---:|---:|",
    ]
    for k, v in d["subagent_top_tools"].items():
        out.append(f"| `{k}` | {v:,} | {_pct(v, d['subagent_tool_calls'])} |")
    return "\n".join(out)


def pricing_table(stats: dict) -> str:
    """Cost, with the rate card and its source stated inline.

    Every figure here must be checkable against the published price list, so
    the rates are printed alongside the totals rather than buried in code.
    """
    from gaia.factory.harvest.scan import PRICING

    tok = stats["tokens"]
    cost = stats["cost_usd"]
    used = set(stats.get("tokens_by_model", {}))

    out = [
        "**Rate card** — USD per million tokens. Source: "
        "[Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing), "
        "retrieved 2026-08-25. Cache multipliers from the same page: 5-minute "
        "write 1.25x base input, 1-hour write 2x, cache read 0.1x. Only the "
        "5-minute write is modelled — transcripts do not record which TTL a "
        "`cache_creation` used, and 5 minutes is the API default.\n",
        "| Model | Input | Output | Cache write (5m) | Cache read | In corpus |",
        "|---|---:|---:|---:|---:|:--:|",
    ]
    for name, r in PRICING.items():
        hit = any(m.lower().startswith(name) for m in used)
        out.append(
            f"| `{name}` | ${r['input']:.2f} | ${r['output']:.2f} | "
            f"${r['write']:.2f} | ${r['read']:.2f} | {'yes' if hit else '—'} |"
        )

    out += [
        "",
        "**Cost of this corpus if run through the API at those rates.**\n",
        "| Token class | Tokens | Cost |",
        "|---|---:|---:|",
        f"| Fresh input | {tok['input']:,} | — |",
        f"| Output | {tok['output']:,} | — |",
        f"| **Uncached input + output** | **{tok['input'] + tok['output']:,}** "
        f"| **${cost['fresh_tokens_only']:,.2f}** |",
        f"| Cache write | {tok['cache_write']:,} | — |",
        f"| Cache read | {tok['cache_read']:,} | — |",
        f"| **Cache write + read** | **{tok['cache_write'] + tok['cache_read']:,}** "
        f"| **${cost['cache_only']:,.2f}** |",
        f"| **All tokens** | **{tok['total']:,}** | **${cost['total_est']:,.2f}** |",
        "",
        f"Per session: p50 ${cost['per_session_p50']:,.2f}, "
        f"p90 ${cost['per_session_p90']:,.2f} (all token classes).",
        "",
        "**Assumptions, all of which change the number:**",
        "",
        "1. Each trace is priced at the model that actually produced its tokens "
        "(`usage_by_model`), not one model applied to the whole session.",
        "2. No Batch API discount (50%) — these were interactive sessions.",
        "3. No volume, enterprise, or negotiated discount.",
        "4. `inference_geo` standard (global); US-only routing would add 1.1x.",
        f"5. Server-tool surcharges excluded — web search bills $10 per 1,000 "
        f"searches on top of tokens, and this corpus made "
        f"{stats['tools'].get('WebSearch', 0):,} `WebSearch` calls "
        f"(~${stats['tools'].get('WebSearch', 0) * 10 / 1000:,.0f} more, if "
        f"each was one search).",
        "6. Unpriced tokens: "
        f"{cost['unpriced_tokens']:,} ({cost['unpriced_pct']}%) — models with no "
        "published rate are excluded rather than guessed.",
        "",
        "> **This is not what was paid.** These sessions ran on a Claude Code "
        "subscription. The figure is what the same token volume would cost at "
        "published API rates — useful for comparing workloads to each other, "
        "not as a bill.",
    ]
    return "\n".join(out)


def scope_token_table(traces: List[dict]) -> str:
    """Tokens and cost split between the orchestrator and its subagents.

    Subagents carry a large share of tool calls; whether they carry a matching share of
    tokens is a different question, and the answer sizes what delegation
    actually costs.
    """
    from gaia.factory.harvest.scan import price_for

    def acc(u: dict, into: Dict[str, int]) -> None:
        into["input"] += u.get("input_tokens", 0)
        into["output"] += u.get("output_tokens", 0)
        into["cache_read"] += u.get("cache_read_tokens", 0)
        into["cache_write"] += u.get("cache_write_tokens", 0)

    def cost(by_model: dict) -> tuple[float, float]:
        fresh = cached = 0.0
        for model, u in by_model.items():
            r = price_for(model)
            if r is None:
                continue
            fresh += (
                u.get("input_tokens", 0) * r["input"]
                + u.get("output_tokens", 0) * r["output"]
            ) / 1_000_000
            cached += (
                u.get("cache_write_tokens", 0) * r["write"]
                + u.get("cache_read_tokens", 0) * r["read"]
            ) / 1_000_000
        return fresh, cached

    main = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    sub = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    main_f = main_c = sub_f = sub_c = 0.0
    main_calls = sub_calls = 0
    sub_sessions = 0
    for t in traces:
        acc(t["usage"], main)
        f, c = cost(t.get("usage_by_model", {}))
        main_f += f
        main_c += c
        main_calls += t["total_calls"]
        if t["subagents"]:
            sub_sessions += 1
        for s in t["subagents"]:
            acc(s["usage"], sub)
            f, c = cost(s.get("usage_by_model", {}))
            sub_f += f
            sub_c += c
            sub_calls += s.get("total_calls", s["steps"])

    mt = sum(main.values())
    st = sum(sub.values())
    tot = mt + st or 1
    rows = [
        ("Input (uncached)", main["input"], sub["input"]),
        ("Output", main["output"], sub["output"]),
        ("Cache write", main["cache_write"], sub["cache_write"]),
        ("Cache read", main["cache_read"], sub["cache_read"]),
    ]
    out = [
        "| Token class | Main sessions | Subagents | Subagent share |",
        "|---|---:|---:|---:|",
    ]
    for name, m, s_ in rows:
        out.append(f"| {name} | {m:,} | {s_:,} | {_pct(s_, m + s_)} |")
    out += [
        f"| **Total tokens** | **{mt:,}** | **{st:,}** | **{_pct(st, tot)}** |",
        "",
        "| Measure | Main sessions | Subagents | Subagent share |",
        "|---|---:|---:|---:|",
        f"| Tool calls | {main_calls:,} | {sub_calls:,} | "
        f"{_pct(sub_calls, main_calls + sub_calls)} |",
        f"| Cost — uncached input + output | ${main_f:,.0f} | ${sub_f:,.0f} | "
        f"{_pct(sub_f, main_f + sub_f)} |",
        f"| Cost — cache write + read | ${main_c:,.0f} | ${sub_c:,.0f} | "
        f"{_pct(sub_c, main_c + sub_c)} |",
        f"| **Cost — total (API-equivalent)** | **${main_f + main_c:,.0f}** | "
        f"**${sub_f + sub_c:,.0f}** | "
        f"**{_pct(sub_f + sub_c, main_f + main_c + sub_f + sub_c)}** |",
        "",
        f"_Across {sub_sessions} sessions that delegated (of {len(traces)})._",
        "",
        "**Column definitions**",
        "",
        "| Row | Definition |",
        "|---|---|",
        '| **Input (uncached)** | Input tokens billed at full rate — not served from the prompt cache. "Uncached" applies only to input; output is always newly generated, so there is no cached-output class. |',
        "| **Output** | Tokens the model generated. |",
        "| **Cache write** | **Input** tokens stored into the prompt cache on first use, billed at 1.25x base input. Not output — input has three billing states (uncached / cache-write / cache-read); output has only one. |",
        "| **Cache read** | Tokens served from the cache, billed at 0.1x base input. |",
        "| **Main sessions** | The orchestrator — the top-level session the human talks to. |",
        "| **Subagents** | Delegated runs spawned by that session, each with its own context. |",
        "| **Subagent share** | Subagent value as a share of main + subagent. |",
    ]
    return "\n".join(out)


def period_line(traces: List[dict]) -> str:
    """The wall-clock span the corpus covers — a cost figure needs a period."""
    stamps = [t["started_at"] for t in traces if t.get("started_at")]
    stamps += [t["last_at"] for t in traces if t.get("last_at")]
    if not stamps:
        return "_period unknown — no timestamps_"
    lo, hi = min(stamps)[:10], max(stamps)[:10]
    from datetime import date

    d0 = date.fromisoformat(lo)
    d1 = date.fromisoformat(hi)
    days = (d1 - d0).days + 1
    return (
        f"_Period: **{lo} to {hi}** — {days} calendar days, "
        f"{len(traces)} sessions (~{len(traces)/days:.1f} sessions/day). "
        "All costs below are for this whole span, not per month._"
    )


# Subagent categories, matched in order. Classified on the delegated goal text
# first, then on the tool mix — a prompt that says "research" but only greps
# the repo is codebase investigation, whatever it called itself.
_SUBAGENT_PATTERNS = [
    ("web research", r"\bresearch\b|\bwebsearch\b|\bwebfetch\b|primary sources"),
    (
        "audit / verify",
        r"read.only (audit|verification)|\baudit\b|\bverify\b|fact.check",
    ),
    ("code review", r"code review|adversarial review|review .*(pr|diff|branch)"),
    (
        "codebase investigation",
        r"find (everything|all|every)|locate|investigate|explore|map the",
    ),
    (
        "doc / transcript work",
        r"transcription|transcript|\bdocx?\b|\bmarkdown\b|summar",
    ),
    ("fix / implement", r"\bfix\b|\bimplement\b|\bapply\b|\brepair\b|\bmigrate\b"),
]


def _subagent_category(goal: str, families: Dict[str, int]) -> str:

    g = " ".join(goal.split()).lower()
    for name, pat in _SUBAGENT_PATTERNS:
        if re.search(pat, g):
            # Trust behaviour over wording when the two disagree.
            if name == "web research" and families.get("web", 0) == 0:
                continue
            return name
    if families.get("web", 0) > families.get("shell", 0):
        return "web research"
    if families.get("edit", 0) or families.get("write", 0):
        return "fix / implement"
    return "other / unclassified"


def subagent_category_table(traces: List[dict]) -> str:
    """What kinds of work get delegated, and what each kind costs."""
    from gaia.factory.harvest.scan import price_for

    cats: Dict[str, dict] = defaultdict(
        lambda: {"n": 0, "tok": 0, "calls": 0, "fresh": 0.0, "cache": 0.0, "ro": 0}
    )
    for t in traces:
        for s in t["subagents"]:
            fam = s.get("families", {})
            cat = _subagent_category(s.get("goal", ""), fam)
            c = cats[cat]
            c["n"] += 1
            c["tok"] += s["usage"].get("total", 0)
            c["calls"] += s.get("total_calls", s["steps"])
            if not fam.get("edit", 0) and not fam.get("write", 0):
                c["ro"] += 1
            for model, u in s.get("usage_by_model", {}).items():
                r = price_for(model)
                if r is None:
                    continue
                c["fresh"] += (
                    u.get("input_tokens", 0) * r["input"]
                    + u.get("output_tokens", 0) * r["output"]
                ) / 1_000_000
                c["cache"] += (
                    u.get("cache_write_tokens", 0) * r["write"]
                    + u.get("cache_read_tokens", 0) * r["read"]
                ) / 1_000_000

    rows = sorted(cats.items(), key=lambda kv: -kv[1]["tok"])
    tot_tok = sum(c["tok"] for _, c in rows) or 1
    tot_n = sum(c["n"] for _, c in rows) or 1
    tot_calls = sum(c["calls"] for _, c in rows) or 1
    out = [
        "| Subagent category | Runs | % runs | Tool calls | % calls | Tokens | "
        "% tokens | Read-only | Cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, c in rows:
        out.append(
            f"| {name} | {c['n']} | {_pct(c['n'], tot_n)} | {c['calls']:,} | "
            f"{_pct(c['calls'], tot_calls)} | {c['tok']/1e6:,.0f}M | "
            f"{_pct(c['tok'], tot_tok)} | {_pct(c['ro'], c['n'])} | "
            f"${c['fresh'] + c['cache']:,.0f} |"
        )
    out.append(
        f"| **total** | **{tot_n}** | **100%** | **{tot_calls:,}** | **100%** | "
        f"**{tot_tok/1e6:,.0f}M** | **100%** | "
        f"**{_pct(sum(c['ro'] for _, c in rows), tot_n)}** | "
        f"**${sum(c['fresh'] + c['cache'] for _, c in rows):,.0f}** |"
    )
    return "\n".join(out)


def glossary_appendix(stats: dict) -> str:
    """Every term used in the tables, defined once."""
    tok = stats["tokens"]
    sessions = stats["sessions"]
    subs = stats["traces_including_subagents"] - sessions
    fresh_pct = 100 * tok["input"] / tok["total"]
    read_pct = 100 * tok["cache_read"] / tok["total"]
    terms = [
        (
            "Session",
            "One top-level Claude Code run — everything between opening the CLI and closing it. Stored as one `<uuid>.jsonl` transcript.",
        ),
        (
            "Subagent",
            "A delegated run spawned by a session via the `Task`/`Agent` tool. Has its own context window and its own transcript under `<session>/subagents/`. Its work is attributed to the parent session.",
        ),
        (
            "Trace",
            "A session or a subagent — the unit this pipeline parses. "
            f"{sessions:,} sessions + {subs:,} subagents = {sessions + subs:,} traces.",
        ),
        (
            "Tool call",
            "One `tool_use` block: the model asking to run `Bash`, `Read`, `Edit`, etc. A single model response can contain several.",
        ),
        (
            "Model call",
            "One API response, identified by a distinct `message.id`. Claude Code writes one JSONL *record per content block*, so records outnumber model calls roughly 2:1 — counting records inflates every token and cost figure.",
        ),
        (
            "User turn",
            "A human message. Harness-injected turns (hook feedback, system reminders — marked `isMeta`) are excluded.",
        ),
        (
            "Token",
            "The unit both billing and context are measured in. Roughly 4 characters of English.",
        ),
        (
            "Input token",
            "Any token sent *to* the model — system prompt, conversation history, tool results, file contents. Has three billing states, below.",
        ),
        (
            "Input (uncached)",
            "Input tokens processed at the full base rate because they were not in "
            f"the prompt cache. In this corpus: {fresh_pct:.1f}% of all tokens.",
        ),
        (
            "Cache write",
            "Input tokens **stored into** the prompt cache on first use, billed at **1.25x** base input. Still input tokens — the 25% premium buys cheap re-reads later.",
        ),
        (
            "Cache read",
            "Input tokens **served from** the prompt cache on a later request, billed "
            f"at **0.1x** base input. The dominant class here at {read_pct:.1f}%.",
        ),
        (
            "Output token",
            "Tokens the model generated. Billed at the output rate. **Output is never cached** — it is new text every time, so unlike input it has only one billing state.",
        ),
        (
            "Prompt cache",
            "Anthropic's prefix cache. A repeated prompt prefix is stored once (cache write) and re-read cheaply (cache read). Any byte change in the prefix invalidates everything after it.",
        ),
        (
            "Fresh $",
            "Cost of uncached input + output only — what a caller with no caching would pay for the same work.",
        ),
        ("Cache $", "Cost of cache writes (1.25x) + cache reads (0.1x)."),
        (
            "API-equivalent cost",
            "What the observed token volume would cost at published API rates. **Not money paid** — these sessions ran on a Claude Code subscription.",
        ),
        (
            "Tool family",
            "A grouping of tool names by what they do: `shell`, `read`, `edit`, `write`, `search`, `web`, `delegate`, `mcp`, `plan`, `meta`.",
        ),
        (
            "Binary invocation",
            "One executable run inside a shell command. `cd x && grep y \\| head` is one tool call but three invocations.",
        ),
        (
            "Resolved / unresolved call",
            "A tool call is resolved once its `tool_result` arrives. Unresolved calls (interrupted or truncated) are excluded from success and failure rates rather than counted as either.",
        ),
        (
            "Fail%",
            "Share of *resolved* tool calls that returned an error. Tool-level failure — **not** task failure; nothing in a transcript records whether the goal was met.",
        ),
        (
            "Thrash / redundant call",
            "Consecutive calls to the same tool with byte-identical arguments. Identity is a hash of the full argument object.",
        ),
        (
            "Repair loop",
            "An edit, then a shell command, then another edit to the **same file** — the fix/verify/fix cycle.",
        ),
        (
            "Corr% (correction)",
            'Share of sessions containing a user turn matching a correction pattern ("that\'s not…", "still broken"). A proxy for friction, not proof of it.',
        ),
        ("Intr% (interrupt)", "Share of sessions the user stopped mid-run."),
        (
            "Deleg%",
            "Share of a use-case's sessions that spawned at least one subagent.",
        ),
        (
            "Use-case",
            "The workflow a session belongs to, assigned by an LLM from the opening instruction. Single-pass, not human-validated.",
        ),
        (
            "p50 / p90",
            "Median and 90th percentile. A large p50→p90 gap means a long tail of outlier sessions.",
        ),
    ]
    out = ["| Term | Definition |", "|---|---|"]
    for name, desc in terms:
        out.append(f"| **{name}** | {desc} |")
    return "\n".join(out)


def caching_explainer(stats: dict) -> str:
    """How prompt caching works, why it dominates this corpus, and how the
    numbers in these tables are computed from it."""
    t = stats["tokens"]
    cr = 100 * t["cache_read"] / t["total"]
    cw = 100 * t["cache_write"] / t["total"]
    return f"""**What it is.** Every API request resends the whole conversation — the API is
stateless, so a session 40 turns deep re-sends all 40 turns. Prompt caching lets Anthropic
store the *prefix* of that prompt after the first request and serve it back cheaply on the
next one, instead of reprocessing it from scratch.

**The three input states.** Input tokens are billed in one of three ways on any given
request. Output tokens have no equivalent — output is generated fresh every time, so it has
a single rate.

| State | What happened | Rate |
|---|---|---|
| **Uncached input** | Sent, processed, not stored and not from cache | 1x base input |
| **Cache write** | Sent and processed, *and* stored for reuse | **1.25x** base input |
| **Cache read** | Not reprocessed — served from the stored prefix | **0.1x** base input |

**Why writes keep happening.** A cache write is not a one-off. The cache stores a prefix;
each turn appends to the conversation, so the *new* portion is not yet cached and gets
written on the next request. A long session therefore pays a small write on nearly every
turn while reading the whole accumulated prefix back at 0.1x. That is why this corpus shows
{cr:.1f}% cache-read alongside a persistent {cw:.1f}% cache-write, rather than one big write and
then nothing.

**Why it pays.** A 5-minute cache write costs 1.25x and each subsequent read costs 0.1x, so
caching breaks even after a single reuse (1.25 + 0.1 = 1.35 versus 2.0 for two uncached
sends). Agentic sessions reuse the same prefix dozens of times, which is why cache reads
dominate.

**How these tables compute it.** The values are not estimated. Every assistant response
reports its own `usage` object with four counters — `input_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens` — and this
pipeline sums them per model, then prices each class at that model's published rate.

**One correctness trap.** Claude Code writes one JSONL *record per content block*, and every
record belonging to the same response repeats the identical `usage` object. Summing per
record double-counts tokens roughly 2x. These tables deduplicate by `message.id` first,
taking the maximum of each counter within a response.

**What is not modelled.** Transcripts record that a cache write happened but not which TTL
was used, so all writes are priced at the 5-minute rate (1.25x). A 1-hour write costs 2x, so
any session using the longer TTL is under-priced here."""


def agent_loop_primer(stats: dict, traces: List[dict]) -> str:
    """What an agentic coding session is, for a reader who has never seen one."""
    tok = stats["tokens"]
    ratio = (tok["input"] + tok["cache_read"] + tok["cache_write"]) / tok["output"]
    cmds = [
        x["arg_digest"]
        for x in all_steps(traces)
        if x["family"] == "shell" and x["arg_digest"]
    ]
    trunc = sum(1 for c in cmds if re.search(r"\b(head|tail)\b", c))
    sub_calls = stats["tool_calls_subagent"]
    sub_tok = sum(
        sub["usage"].get("total", 0) for t in traces for sub in t["subagents"]
    )
    haiku_subs = sum(
        1
        for t in traces
        for sub in t["subagents"]
        if any("haiku" in m.lower() for m in (sub.get("usage_by_model") or {}))
    )
    p50 = stats["steps_per_session"]["p50"]
    fail_pct = 100 * stats["failure_rate"]
    tcpm = stats["tool_calls_per_model_call"]
    trunc_pct = 100 * trunc / len(cmds)
    sub_call_pct = 100 * sub_calls / stats["total_tool_calls"]
    sub_tok_pct = 100 * sub_tok / tok["total"]
    return f"""Everything in this report describes **agentic coding sessions**. If that term is
new, this section is the minimum needed to read the rest.

**The basic loop.** A developer gives an instruction ("fix the failing test"). The model
cannot touch the machine directly — it can only emit *tool calls*, structured requests like
"run `pytest -x`" or "read `src/app.py`". The harness (here, Claude Code) executes each
request and feeds the result back. The model reads that result, decides what to do next,
and emits another call. This repeats until the model stops asking.

```
  human instruction
        │
        ▼
   ┌─ MODEL ─────────────┐   emits a tool call
   │  reads the whole    │──────────────────────▶ HARNESS executes it
   │  history each turn  │◀────────────────────── returns the result
   └─────────────────────┘   result appended to history
        │  (repeats — median {p50} tool calls in this corpus)
        ▼
   final answer
```

**Why the token numbers look the way they do.** The API is stateless: the model has no
memory between calls, so the harness resends the *entire* conversation every time — the
instruction, every prior tool call, and every result. A session 100 calls deep resends all
100. That is why input tokens outnumber output tokens ~{ratio:.0f}:1 here, and why prompt caching
(Appendix B) dominates the cost.

**Tool call vs model call.** One model call is one API response. That response may contain
several tool calls (run three greps at once), or none (just text). This corpus averages
{tcpm} tool calls per model call.

**Subagents.** The model can delegate: spawn a second agent with its own fresh context,
give it a task, and receive only its conclusion. The delegating session never sees the
subagent's intermediate work, which is the point — a 40-call investigation returns as one
paragraph. Subagents here carry {sub_call_pct:.0f}% of all tool calls but only {sub_tok_pct:.0f}% of tokens, because each
starts small instead of inheriting the parent's accumulated history.

**Context window.** The model can only hold so much at once — 1M tokens for Opus 5, Opus 4.8 and
Sonnet 5, but 200K for Haiku 4.5, which ran {haiku_subs} of the subagent traces here. Long sessions push against it, which is why the corpus shows the model truncating
its own command output {trunc_pct:.1f}% of the time — it is rationing space it cannot see.

**Why failures are normal.** A tool call can fail — a command exits non-zero, a path does
not exist, a timeout fires. The failure is returned like any other result and the model
adapts. A {fail_pct:.1f}% failure rate is not {fail_pct:.1f}% of tasks going wrong; it is individual
steps erroring inside runs that mostly still succeed."""


def methodology_primer(stats: dict, traces: List[dict]) -> str:
    """Where the data came from, how it was processed, and what it cannot say."""
    cmds = [
        x["arg_digest"]
        for x in all_steps(traces)
        if x["family"] == "shell" and x["arg_digest"]
    ]
    lead_cd = sum(1 for c in cmds if _binaries(c)[:1] == ["cd"])
    n_cmds = len(cmds)
    cd_pct = 100 * lead_cd / n_cmds
    total_calls = stats["total_tool_calls"]
    stamps = sorted(
        s for t in traces for s in (t.get("started_at"), t.get("last_at")) if s
    )
    first = datetime.fromisoformat(stamps[0].replace("Z", "+00:00"))
    last = datetime.fromisoformat(stamps[-1].replace("Z", "+00:00"))
    days = (last.date() - first.date()).days + 1
    roots = {re.sub(r"--claudia-worktrees.*$", "", t["project"]) for t in traces}
    n_repos = len(roots)
    sub_pct = stats["subagent_share_pct"]
    return f"""**The raw data.** Claude Code writes a complete JSONL transcript of every session to
`~/.claude/projects/<slugified-cwd>/<session-uuid>.jsonl`, and one per delegated run under
`<session-uuid>/subagents/`. Each line is one record: a human turn, a model response, or a
tool result. Nothing is sampled: every transcript in the period is read. Files with no
assistant activity (aborted or metadata-only runs) are skipped, since they
contain no work to measure.

**The pipeline.** Three deterministic stages, no LLM and no network:

| Stage | Module | What it does |
|---|---|---|
| Parse | `harvest/reader.py` | JSONL to a normalized `Trace` — ordered tool calls, outcomes, token usage, subagents attached to their parent |
| Aggregate | `harvest/scan.py` | Corpus-wide counts, token totals, per-model cost |
| Render | `harvest/report.py` | Every table in Part 1 |

**The one LLM step.** Assigning each session to a use-case (`pr_lifecycle`, `code_review`,
…) was done by classifying the opening instruction with an LLM, single-pass, against a
fixed taxonomy. It was *not* human-validated. Everything else is arithmetic over the
transcripts.

**Three things this data cannot tell you.**

1. **Whether any task succeeded.** Transcripts record what happened, never whether the goal
   was met. `Fail%` counts tool-call errors. A session can end with every tool call
   succeeding and the work still wrong — and the reverse. **No number here is a success
   rate.**
2. **Whether friction was real.** `Corr%` and `Intr%` are pattern matches on user turns —
   phrases like "that's not what I asked", or a run being stopped. They are evidence of
   friction, not proof. Compare them *between* use-cases; do not read absolutes.
3. **Whether this generalises.** One developer, ~{n_repos} repositories, {days} days
   ({first:%d %b} to {last:%d %b %Y}). It is a portrait of one person's usage, not of
   developers.

**How to read a percentage here.** Every table states its denominator, because the same raw
count means different things against different bases. "{sub_pct:.0f}% of tool calls" is out of {total_calls:,}
corpus-wide including subagents; "{cd_pct:.1f}% of shell commands start with `cd`" is out of
{n_cmds:,} shell commands only. When two figures seem to disagree, check which population each is
over.

**Reproducing it.** Re-running the two commands at the top of Part 1 regenerates every
table. It will not reproduce these figures *exactly* — the corpus grows while it is being
analysed, since the session doing the analysis is itself being recorded. Counts drift by
tens of calls between runs; the shape is stable."""


if __name__ == "__main__":
    main()
