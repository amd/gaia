# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""One self-contained report: the taxonomy, the evidence, and every definition.

Written to stand alone. A reader who has never seen this corpus, this tool, or
the word "agent" should be able to start at the top and never need another file:
every term is defined before it is used, every number states how it was obtained,
and every estimate says so.

Usage::

    python -m gaia.factory.harvest.usecase_report --cache DIR > report.md
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from gaia.factory.harvest import inventory as inv
from gaia.factory.harvest import use_cases as uc
from gaia.factory.harvest.tasks import segment

# --------------------------------------------------------------------------- glossary

#: Plain-language definitions for the structural vocabulary. Printed first,
#: because every table below is meaningless without them.
TERMS: Sequence[tuple] = (
    (
        "session",
        "One continuous working period with the agent, from the moment it is "
        "started to the moment it is closed. A session can contain many "
        "unrelated requests, which is why it is the wrong unit to analyse.",
    ),
    (
        "ask / prompt",
        "One message sent to the agent. Usually typed by a person, but not "
        "always — see *origin* below.",
    ),
    (
        "task",
        "One ask together with everything the agent did in response to it. "
        "**This is the unit of analysis throughout this report.** A session "
        "holding eight requests contains eight tasks.",
    ),
    (
        "step / tool call",
        "One action the agent takes: reading a file, running a command, "
        "searching the web. The agent alternates between thinking and taking "
        "steps until it decides the task is done.",
    ),
    (
        "tool family",
        "Steps grouped by what they do to the world, regardless of which tool "
        "did it: `read`, `search`, `edit`, `write`, `shell`, `web`, `task`. "
        "Useful because `edit` and `write` mean the agent changed something, "
        "and the others mean it only looked.",
    ),
    (
        "mutating task",
        "A task in which the agent changed at least one file. The opposite is "
        "a read-only task, which produces an answer or a report but leaves "
        "everything as it found it.",
    ),
    (
        "activity",
        "*What kind of work* a task is, independent of subject: implementing, "
        "reviewing, debugging. Deliberately abstract, so that implementing "
        "means the same thing whether the subject is a parser or a policy "
        "document. Full list and definitions below.",
    ),
    (
        "domain",
        "*What the work was about*: software, documentation, security, and so "
        "on. Independent of activity — reviewing and implementing can both "
        "happen in any domain. Full list and definitions below.",
    ),
    (
        "use case",
        "The specific, named job someone wanted done — 'CI failure triage', "
        "'meeting transcript work'. More concrete than activity and domain, "
        "and the level at which an evaluation can actually be written. Full "
        "list, definitions and statistics below.",
    ),
    (
        "track",
        "A grouping of related use cases: build, verify, operate, content, "
        "analyse, decide, agent. Seven of them, defined below.",
    ),
    (
        "origin",
        "Who produced the ask. `human` means a person typed it. `automation` "
        "means a machine raised it — for example an alert saying a build has "
        "failed; the work is real, but nobody asked. `boilerplate` means a "
        "system nudge carrying no request at all, which is excluded from every "
        "count in this report.",
    ),
    (
        "subagent / delegation",
        "The agent can start a second copy of itself to handle a piece of work "
        "in a separate context, then read back only the summary. This keeps "
        "the main context small, and it is billed separately.",
    ),
    (
        "cache read",
        "Sending a model the same text twice is normal — an agent resends the "
        "whole conversation on every step. Repeated text can be served from a "
        "cache at roughly a tenth of the price. A high cache-read share is "
        "expected and healthy, not waste.",
    ),
    (
        "apportioned",
        "Billing is recorded once per **session**, but this report analyses "
        "**tasks**. Where a figure is marked apportioned, a session's total was "
        "divided among its tasks in proportion to their tool calls. Corpus "
        "totals are exact; per-use-case token figures are estimates.",
    ),
    (
        "result_chars",
        "How many characters a single tool call returned. Useful for comparing "
        "which use cases pull the most material into context, but it is **only "
        "part** of what the model sees — it excludes the system prompt, the "
        "conversation so far, and file contents already held. Do not use it to "
        "size hardware; see *prompt size*.",
    ),
    (
        "prompt size",
        "Every token the model had to attend over on a single request — system "
        "prompt, full conversation, all held context. Measured directly from "
        "billing records as `input + cache_read + cache_write`. **This is the "
        "number that sizes a context window and the memory behind it**, and it "
        "is roughly 4.6x larger than tool output alone suggests.",
    ),
)

#: What each activity means. The keys match `tasks.ACTIVITY_PATTERNS`.
ACTIVITY_DEFS: Dict[str, str] = {
    "review": "Reading something and judging whether it is correct, safe or "
    "complete. Produces findings; changes nothing by default.",
    "answer": "Responding to a direct question. Short, and often uses no tool "
    "at all.",
    "implement": "Building something that did not exist, or repairing "
    "something that does. The one activity that changes files by default.",
    "author": "Producing prose or visuals for a person to read — documents, "
    "reports, slides.",
    "debug": "Working out why something is behaving wrongly. Starts from a "
    "symptom rather than from a specification.",
    "operate": "Running the machinery: pushing, merging, deploying, "
    "restarting, checking status.",
    "research": "Going outside what is already at hand for information, "
    "usually to the web, and reporting what was found.",
    "test": "Writing, running or extending automated checks.",
    "refactor": "Reorganising existing work without changing what it does.",
    "release": "Publishing: version bumps, changelogs, tags, build artefacts.",
    "configure": "Getting a machine, service or account into a usable state.",
    "extract": "Pulling structure out of unstructured input, or converting "
    "between formats.",
    "unclassified": "No pattern matched and behaviour gave no signal. Reported "
    "rather than hidden, because the size of this bucket is how you judge "
    "whether the taxonomy covers the corpus.",
}

#: What each domain means. Keys match `tasks.DOMAIN_PATTERNS`.
DOMAIN_DEFS: Dict[str, str] = {
    "agent_self": "The agent system itself — its prompts, skills, tools, "
    "memory and orchestration. Large here because this corpus is an agent "
    "being used to build an agent. **That is a property of this corpus, not a "
    "finding about agent use in general.**",
    "software": "Application and library code: functions, modules, APIs, the "
    "programs themselves.",
    "infra_ci": "The machinery that builds, tests, packages and ships the "
    "software — pipelines, runners, containers, installers.",
    "product": "What should be built and in what order — roadmaps, "
    "milestones, issues, priorities, customers.",
    "docs": "Written material for people: guides, references, READMEs, "
    "changelogs, slides.",
    "security": "Vulnerabilities, credentials, permissions, isolation.",
    "data": "Datasets, corpora, transcripts, metrics, benchmark results.",
    "comms": "Correspondence and scheduling — email, chat, meetings.",
    "unclassified": "Neither the wording nor the tools placed it.",
    "boilerplate": "Excluded system nudges; see *origin*.",
}


#: The synthesis. Kept as prose rather than generated, because it is an argument
#: rather than a measurement — but every claim names the section that supports
#: it, so a disagreeing reader can check the number rather than the opinion.
ARCHITECTURE = """
### 8.1 What kind of workload this is

Three measurements decide the shape of the machine, and all three point the
same way.

| finding | measured | consequence |
|---|---|---|
| **Prefill dominates** | 289 input tokens for every 1 generated (§7.1) | The workload is **compute-bound on prefill**, not memory-bandwidth-bound on generation. This inverts the usual chatbot assumption, and it means matrix throughput matters more than memory bandwidth. |
| **Most prefill is repeated text** | 94% of input is cache reads (§7.1) | **Prefix caching is not an optimisation — it is the architecture.** Without it the same work costs ~17x the prefill compute. |
| **Context is enormous** | median request 153K tokens, p90 434K, max 1.0M (§6) | Memory is dominated by the KV cache, not by model weights. At these lengths one agent's cache exceeds the size of the model serving it. |

**The workload is also bursty and mostly idle.** Inference occupies only 15% of
wall-clock minutes (§7.3), and the typical busy minute serves 3 requests from 1
session. Sizing for continuous load over-provisions by roughly 5.6x.

### 8.2 The memory arithmetic, and the two levers

Because a single model instance loads its weights once and then pays KV cache
per concurrent context, fleet memory is `weights + N x KV`. The KV term
dominates, which leaves exactly two levers with real leverage:

1. **Shorten the working context.** Halving context halves fleet memory — the
   same saving as halving the number of agents. This makes context discipline
   (truncating tool output, delegating to subagents, evicting stale material) a
   *hardware* decision, not a tidiness one.
2. **Change the attention geometry.** Sliding-window attention caps the layers
   that grow with length. In §7.6 the sliding-window model needs **8.6 GiB** at
   the p90 context where a comparable dense model needs **36.3 GiB** — a 4x
   difference from architecture alone, before any quantisation.

Quantising the cache to `q8_0` roughly halves it again, and at these context
lengths that is what makes a deployment fit at all rather than a tuning choice.

### 8.3 The recommended shape

**A three-tier split, because the work is not homogeneous.** §4 shows use cases
differing by an order of magnitude in length and cost, and §7.2 shows this
corpus ran 94% of its tokens on a single frontier tier — which is what you do
when routing is unavailable, not evidence that one tier is right.

| tier | serves | why |
|---|---|---|
| **Local, small, long-context** | Question answering, repository operations, CI triage, issue triage — the short read-mostly majority | §4: these are 6–8 median actions and a small share of tokens. They are latency-sensitive and privacy-relevant, and they do not need frontier reasoning. |
| **Local, larger** | Code review, document audit, data analysis — verification work | §4: read-dominated and mid-length. Verification benefits from stronger reasoning but produces little output, so it suits a prefill-heavy local accelerator. |
| **Frontier, remote or large local** | Feature implementation, bug fixing, refactoring — long multi-step build work | §4: 28–102 median actions, p90 up to 237, and 40%+ of all tokens. These are the tasks that fail expensively when the model is weak. |

**Routing is by use case, and the taxonomy in §3–5 is what makes that
possible** — it is the same classification, applied at request time rather than
in analysis.

### 8.4 Software requirements, in priority order

These follow from the measurements and are ordered by how much they change.

1. **Prefix caching across requests, with a long-lived cache.** §7.1 makes this
   the single highest-leverage feature: 94% of input is text the model has
   already seen. A server without it does ~17x the prefill work.
2. **KV-cache quantisation (`q8_0` at minimum).** §7.6: the fp16 figures are not
   deployable at any realistic concurrency.
3. **A context-length budget per use case, enforced.** §6 and §8.2: context is
   the fleet-wide memory lever. Uniform budgets are either wasteful or broken,
   because the use cases differ by 10x.
4. **Tool-output truncation with a stated policy.** §6: a single tool result can
   exceed 20K tokens. Where truncation falls determines what the agent can still
   do, so it must be a designed behaviour, not a buffer limit.
5. **A broad shell surface, or an escape hatch.** §9: the top ten programs are
   73% of calls, but thousands were used exactly once. An allow-list built from
   common cases blocks a long tail of real needs.
6. **Delegation as a first-class memory strategy.** §7.5: subagents carry their
   own context and return only a summary. This is how a long task stays inside a
   context budget.
7. **A machine-driven intake path.** §2: 6% of real work arrived from automation
   rather than a person. An agent that only accepts typed input cannot serve it.
8. **Per-step timing and per-request instrumentation.** §10: the corpus cannot
   separate local tool execution from inference, so the CPU-versus-accelerator
   balance is unmeasurable. That is the highest-value missing measurement.

### 8.5 What this analysis still cannot tell you

Stated so the recommendation is not read as more certain than it is.

- **No latency data.** Nothing here says how long a request took, so nothing
  here sizes for a latency target. Throughput requirements (§7.4) are derived
  from counts over time, not from measured service times.
- **The local-versus-frontier tiering in §8.3 is a proposal, not a result.**
  This corpus ran almost entirely on one frontier model; it shows which work is
  short and read-mostly, but it does not demonstrate that a smaller model
  succeeds at it. **Validating that is an evaluation, not an analysis** — and it
  is exactly what the use-case definitions in §5 were written to support.
- **One operator, one subject.** §10. The distribution of work is specific to
  this corpus. The per-request sizes, the prefill ratio and the cache-read share
  are properties of how agents work, and generalise better than the mix does.
"""


def _pctl(sorted_vals: List[int], q: int) -> int:
    return sorted_vals[min(len(sorted_vals) - 1, q * len(sorted_vals) // 100)]


def _request_sizes(cache: Path) -> List[int]:
    """Prompt size of every API request, if the measurement has been frozen.

    Produced by ``harvest.context.collect``, which re-reads the raw transcripts
    and records ``input + cache_read + cache_write`` per request. Absent when the
    cache has not been measured yet; the report then omits the absolute sizing
    rather than substituting the weaker proxy for it.
    """
    f = cache / "requests.json"
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8")).get("requests", [])


def _k(tokens: float) -> str:
    """Thousands, without rounding a real value down to a bare ``0K``."""
    if tokens >= 1000:
        return f"{tokens/1000:,.0f}K"
    return f"{tokens:,.0f}" if tokens else "0"


def _tbl(rows: List[List[str]], head: List[str], align: str = "") -> str:
    align = align or "l" * len(head)
    rule = (
        "|" + "|".join({"l": "---", "r": "---:", "c": ":---:"}[a] for a in align) + "|"
    )
    out = ["| " + " | ".join(head) + " |", rule]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def build(
    traces: Sequence[dict],
    cache_dir: Optional[Path] = None,
    systems_md: str = "",
) -> str:
    tasks_all = [t for tr in traces for t in segment(tr)]
    uc.label_all(tasks_all)
    tasks = [t for t in tasks_all if t.is_work]
    boiler = len(tasks_all) - len(tasks)
    P = uc.profile(tasks, traces)
    inventory = inv.build(tasks)
    cov = inv.coverage(inventory)

    total_tokens = sum((tr.get("usage") or {}).get("total", 0) for tr in traces)
    total_steps = sum(t.n_steps for t in tasks)
    worked = [t for t in tasks if t.n_steps]
    auto = sum(1 for t in tasks if t.origin == "automation")

    L: List[str] = []
    A = L.append

    # ---------------------------------------------------------------- preamble
    A("# What a working agent is actually used for")
    A("")
    A(
        f"An analysis of **{len(traces)} real working sessions** — every request made, "
        f"every action taken, what it cost and what it required. "
        f"The corpus contains **{len(tasks_all):,} requests**, of which "
        f"**{len(tasks):,}** are real units of work, driving **{total_steps:,} actions** "
        f"and **{total_tokens/1e9:.2f} billion tokens** of model traffic."
    )
    A("")
    A(
        "The purpose is to answer one question: *what would an agent have to be "
        "good at to do this work?* The answer is organised as **24 named use "
        "cases**, each defined, measured, and stated in terms of what a test of "
        "it would look like."
    )
    A("")
    A("---")
    A("")
    A("## 1. How to read this report")
    A("")
    A(
        "Nothing below assumes prior knowledge. These are the terms used "
        "throughout, defined before first use."
    )
    A("")
    A(_tbl([[f"**{k}**", v] for k, v in TERMS], ["term", "meaning"]))
    A("")
    A("### The three levels of labelling")
    A("")
    A(
        "Every task carries three labels, from most abstract to most concrete. "
        "They answer different questions and are used for different things."
    )
    A("")
    A(
        _tbl(
            [
                [
                    "**activity**",
                    "13",
                    "What kind of work is this?",
                    "`implement`",
                    "Comparing effort across subjects",
                ],
                [
                    "**domain**",
                    "10",
                    "What was it about?",
                    "`infra_ci`",
                    "Knowing which systems are touched",
                ],
                [
                    "**use case**",
                    "24",
                    "What specific job was this?",
                    "`ci_failure_triage`",
                    "**Writing an evaluation**",
                ],
            ],
            ["level", "count", "question it answers", "example", "what it is for"],
        )
    )
    A("")
    A(
        "> **Why three and not one.** Activity and domain are abstract enough to "
        "compare across a whole corpus, but too abstract to test: `author` + "
        "`docs` covers writing a README, fact-checking a specification and "
        "building a slide deck, and those need different tools and different "
        "measures of success. The use case is the level at which a test can be "
        "written."
    )
    A("")
    A("### How a task gets its labels")
    A("")
    A(
        "Labelling is **deterministic keyword matching**, not a model making a "
        "judgement. This is a deliberate trade: a model would label more subtly, "
        "but rerunning it on a second corpus would produce a fresh set of "
        "judgement calls rather than comparable numbers. Three rules apply, in "
        "order:"
    )
    A("")
    A(
        "1. **First match wins**, most specific first. Real asks are compound — "
        '"refactor the loader, fix the failing tests and push the PR" touches '
        "three categories and gets one label. The most precise verb wins, so "
        "`refactor` is matched before `bug_fix` and `pr_triage`. *(Ordered the "
        "other way, 32 of 35 refactoring asks were absorbed by those two.)*"
    )
    A(
        "2. **Behaviour overrides wording.** An ask that says "
        '"research the options" but made no web call and rewrote four files was '
        "not research. What the agent did outranks what the person wrote."
    )
    A(
        "3. **Follow-ups inherit.** A turn like "
        '"just push it" continues the task before it and is labelled '
        "accordingly, marked as inherited so it can be excluded as weaker "
        "evidence."
    )
    A("")

    # ---------------------------------------------------------------- corpus
    A("---")
    A("")
    A("## 2. The corpus")
    A("")
    A(
        _tbl(
            [
                ["Sessions", f"{len(traces):,}", "Continuous working periods"],
                [
                    "Requests received",
                    f"{len(tasks_all):,}",
                    "Every message sent to the agent",
                ],
                [
                    "— real units of work",
                    f"**{len(tasks):,}**",
                    "**The task count used everywhere below**",
                ],
                [
                    "— system boilerplate, excluded",
                    f"{boiler:,}",
                    "Nudges carrying no request; see below",
                ],
                [
                    "Tasks that used at least one tool",
                    f"{len(worked):,}",
                    f"The rest were answered from context ({len(tasks)-len(worked):,})",
                ],
                ["Actions taken", f"{total_steps:,}", "Tool calls"],
                [
                    "Model traffic",
                    f"{total_tokens/1e9:.2f}B tokens",
                    "Exact, from billing records",
                ],
                [
                    "Requests per session",
                    f"median {sorted(len(segment(t)) for t in traces)[len(traces)//2]}",
                    "**A session is not a task** — see below",
                ],
            ],
            ["", "value", "meaning"],
            "lrl",
        )
    )
    A("")
    A("### A session is not a task, and not every request is human")
    A("")
    A(
        f"Two corrections underpin everything that follows, and both change the "
        f"numbers materially."
    )
    A("")
    A(
        f"**First, a session holds many requests.** {len(traces)} sessions carry "
        f"{len(tasks_all):,} of them. Any analysis that labels a *session* assigns "
        f"one label to work that changed subject several times — a session that "
        f"began as a code review and ended as a release is counted as whichever "
        f"came first. Every figure in this report is attributed to the specific "
        f"request that caused it."
    )
    A("")
    A(
        f"**Second, {boiler + auto} requests were not typed by a person.** Tooling "
        f"injects messages into the same channel a human uses, and they were being "
        f"read as though someone had written them:"
    )
    A("")
    A(
        _tbl(
            [
                [
                    "`boilerplate`",
                    f"{boiler}",
                    "A system nudge with no request in it",
                    "**Excluded.** It is not work.",
                ],
                [
                    "`automation`",
                    f"{auto}",
                    "A machine-raised alert, e.g. a build failed",
                    "**Counted and tagged.** The work is real; nobody asked for it.",
                ],
            ],
            ["origin", "count", "what it is", "treatment"],
            "lrll",
        )
    )
    A("")
    A(
        "> **Why this mattered.** One recurring nudge contained the phrase "
        "`claudia_rename_task`, and the word *rename* is part of the pattern for "
        "refactoring work. That single collision made **80% of the `refactor` "
        "category** an artefact of the orchestrator talking to itself. A separate "
        "collision put build-failure alerts into `debug`, inflating it by 39%. "
        "Both are corrected here."
    )
    A("")
    A(
        f"That **{100*auto/len(tasks):.0f}% of real work arrives without a human "
        f"asking** is itself a finding: an agent in this role needs to accept work "
        f"from machines as a first-class path, not only from a person at a keyboard."
    )
    A("")

    # ---------------------------------------------------------------- taxonomy defs
    A("---")
    A("")
    A("## 3. The taxonomy, defined")
    A("")
    A("### 3.1 Activities — what kind of work")
    A("")
    counts = Counter(t.activity for t in tasks)
    A(
        _tbl(
            [
                [
                    f"`{k}`",
                    f"{counts.get(k, 0)}",
                    f"{100*counts.get(k, 0)/len(tasks):.1f}%",
                    v,
                ]
                for k, v in sorted(
                    ACTIVITY_DEFS.items(), key=lambda kv: -counts.get(kv[0], 0)
                )
                if counts.get(k, 0)
            ],
            ["activity", "tasks", "share", "definition"],
            "lrrl",
        )
    )
    A("")
    A("### 3.2 Domains — what the work was about")
    A("")
    dcounts = Counter(t.domain for t in tasks)
    A(
        _tbl(
            [
                [
                    f"`{k}`",
                    f"{dcounts.get(k, 0)}",
                    f"{100*dcounts.get(k, 0)/len(tasks):.1f}%",
                    v,
                ]
                for k, v in sorted(
                    DOMAIN_DEFS.items(), key=lambda kv: -dcounts.get(kv[0], 0)
                )
                if dcounts.get(k, 0)
            ],
            ["domain", "tasks", "share", "definition"],
            "lrrl",
        )
    )
    A("")
    A("### 3.3 Tracks — families of use case")
    A("")
    tr_counts: Counter = Counter()
    for k, v in P.items():
        tr_counts[v["track"]] += v["tasks"]
    A(
        _tbl(
            [
                [
                    f"`{k}`",
                    f"{tr_counts.get(k, 0)}",
                    f"{100*tr_counts.get(k, 0)/len(tasks):.1f}%",
                    v,
                ]
                for k, v in sorted(
                    uc.TRACKS.items(), key=lambda kv: -tr_counts.get(kv[0], 0)
                )
            ],
            ["track", "tasks", "share", "definition"],
            "lrrl",
        )
    )
    A("")

    # ---------------------------------------------------------------- master table
    A("---")
    A("")
    A("## 4. The 24 use cases, measured")
    A("")
    A(
        "Sorted by token consumption — that is, by what each use case actually "
        "costs to serve, which is a better guide to where engineering effort pays "
        "off than task count alone."
    )
    A("")
    A("**Column meanings:**")
    A("")
    A(
        _tbl(
            [
                ["tasks", "How many distinct requests fell into this use case."],
                ["% of work", "That count as a share of all 1,432 real tasks."],
                [
                    "median / p90 steps",
                    "Tool calls in a typical task, and in a heavy one. "
                    "*p90* means 9 in 10 tasks are at or below this.",
                ],
                [
                    "tokens",
                    "Model traffic attributed to this use case. **Apportioned "
                    "estimate** — see the glossary.",
                ],
                [
                    "% tok",
                    "Share of all model traffic. Compare against *% of work* to "
                    "see which use cases are disproportionately expensive.",
                ],
                [
                    "writes",
                    "Share of tasks that changed at least one file. Low means "
                    "the use case is read-only — it produces understanding, not "
                    "artefacts.",
                ],
                [
                    "fail",
                    "Share of tool calls that returned an error. Retries and "
                    "dead ends both land here.",
                ],
                [
                    "complexity",
                    "Rank from 1 (most demanding) to 23. A composite of six "
                    "measured signals — see §4.1. **† marks a use case "
                    "with under 20 tasks**, where the inputs are not stable.",
                ],
            ],
            ["column", "meaning"],
        )
    )
    A("")
    rows = []
    for k, v in sorted(P.items(), key=lambda kv: -kv[1]["tokens"]):
        if k in ("unclassified", "boilerplate"):
            continue
        rows.append(
            [
                f"**{v['label']}**",
                f"`{v['track']}`",
                f"{v['tasks']}",
                f"{v['task_pct']:.1f}%",
                f"{v['median_steps']}",
                f"{v['p90_steps']}",
                f"{v['tokens']/1e6:,.0f}M",
                f"{v['token_pct']:.1f}%",
                f"{v['mutating_pct']:.0f}%",
                f"{v['failure_pct']:.1f}%",
                f"**{v['complexity_rank']}**"
                + ("" if v.get("complexity_reliable") else "&nbsp;&dagger;"),
            ]
        )
    A(
        _tbl(
            rows,
            [
                "use case",
                "track",
                "tasks",
                "% of work",
                "median steps",
                "p90 steps",
                "tokens",
                "% tok",
                "writes",
                "fail",
                "complexity",
            ],
            "llrrrrrrrrr",
        )
    )
    A("")
    A("### 4.1 How complexity is ranked")
    A("")
    A(
        "Task count says how *often* work happens, not how *hard* it is. The rank "
        "combines six measured signals, each rank-normalised across use cases and "
        "averaged with equal weight:"
    )
    A("")
    A(
        _tbl(
            [[f"`{k}`", v] for k, v in uc.COMPLEXITY_SIGNALS.items()],
            ["signal", "why it counts toward difficulty"],
        )
    )
    A("")
    A(
        "> **This is a construct, not a measurement.** It is **ordinal**: it says "
        "refactoring is more demanding than answering a question, not that it is "
        "four times more demanding. Ranks are used rather than raw values so one "
        "heavy-tailed signal cannot dominate. Every input is printed in the table "
        "above, so a reader who disagrees with the weighting can recompute it."
    )
    A("")
    ranked_c = sorted(
        (v for k, v in P.items() if "complexity_rank" in v),
        key=lambda v: v["complexity_rank"],
    )
    A(
        _tbl(
            [
                [
                    f"**{v['complexity_rank']}**",
                    v["label"] + ("" if v.get("complexity_reliable") else " &dagger;"),
                    f"{v['median_steps']} / {v['p90_steps']}",
                    f"{v['tokens_per_task']/1000:,.0f}K",
                    f"{_k(v['median_task_chars'] / uc.CHARS_PER_TOKEN)}",
                    f"{v['failure_pct']:.1f}%",
                    f"{v['delegation_per_task']:.2f}",
                ]
                for v in ranked_c
            ],
            [
                "rank",
                "use case",
                "steps med / p90",
                "tokens per task",
                "context pulled in",
                "fail rate",
                "delegations",
            ],
            "rlrrrrr",
        )
    )
    A("")
    A(
        "_&dagger; fewer than 20 tasks — ranked like any other but the inputs are "
        "not stable, so treat the position as indicative._"
    )
    A("")
    A(
        "**What the ranking shows.** The demanding work is *restructuring* work — "
        "refactoring, bug fixing and feature building take the top three places, "
        "and they are also the three that most often had to delegate to a subagent "
        "to stay inside a context budget. The cheap end is not trivial work, it is "
        "*short* work: answering a question, triaging an issue, reacting to a "
        "failed build. **A harness tuned for the cheap end will fail the expensive "
        "end**, which is the argument for per-use-case step and context budgets."
    )
    A("")
    u = P.get("unclassified", {})
    A(
        f"_{u.get('tasks', 0)} tasks ({u.get('task_pct', 0):.1f}%) matched no pattern "
        "and are excluded from the table above. That figure is the honest measure "
        "of how completely this taxonomy covers the corpus._"
    )
    A("")

    # ---------------------------------------------------------------- deep dives
    A("---")
    A("")
    A("## 5. Each use case in detail")
    A("")
    A(
        "For each: what it is, what a test of it would have to do, what it cost, "
        "what it demanded of the machine, and real examples of how it was asked."
    )
    A("")
    for k, v in sorted(P.items(), key=lambda kv: -kv[1]["tokens"]):
        if k in ("unclassified", "boilerplate"):
            continue
        ex = uc.examples(tasks, k, n=3)
        A(f"### {v['label']}")
        A("")
        A(
            f"`{k}` · track `{v['track']}` · **{v['tasks']} tasks** ({v['task_pct']:.1f}% of work)"
        )
        A("")
        A(f"**What it is.** {v['definition']}")
        A("")
        A(f"**What an evaluation must do.** {v['evaluates']}")
        A("")
        A(
            _tbl(
                [
                    [
                        "Volume",
                        f"{v['tasks']} tasks · {v['steps']:,} actions "
                        f"({v['step_pct']:.1f}% of all actions)",
                    ],
                    [
                        "Task length",
                        f"median **{v['median_steps']}** actions · "
                        f"p90 {v['p90_steps']} · longest {v['max_steps']}",
                    ],
                    [
                        "Cost (apportioned est.)",
                        f"{v['tokens']/1e6:,.0f}M tokens ({v['token_pct']:.1f}% of all) · "
                        f"~{v['tokens_per_task']/1000:,.0f}K per task",
                    ],
                    [
                        "Changes files",
                        f"{v['mutating_pct']:.0f}% of tasks"
                        + (" — read-only by nature" if v["mutating_pct"] < 25 else ""),
                    ],
                    [
                        "Raised by automation",
                        f"{v['automation_pct']:.0f}% of tasks",
                    ],
                    [
                        "Tool-call failure rate",
                        f"{v['failure_pct']:.1f}%",
                    ],
                    [
                        "Context demand (est.)",
                        f"typical task ingests ~{_k(v['median_task_chars'])} chars · "
                        f"heavy task ~{_k(v['p90_task_chars'])} · "
                        f"peak single task ~**{_k(v['est_peak_ctx_tokens'])} tokens**",
                    ],
                    [
                        "Largest single tool result",
                        f"{v['max_result_chars']:,} chars "
                        f"(~{v['max_result_chars']//uc.CHARS_PER_TOKEN:,} tokens) · "
                        f"99th pct {v['p99_result_chars']:,}",
                    ],
                    [
                        "Complexity rank",
                        f"**{v['complexity_rank']} of {len(ranked_c)}**"
                        + (
                            ""
                            if v.get("complexity_reliable")
                            else " (under 20 tasks — indicative only)"
                        ),
                    ],
                    [
                        "Delegation",
                        f"{v['delegation_per_task']:.2f} subagents per task (est.)",
                    ],
                    [
                        "Human interruptions",
                        f"{v['interrupts_per_task']:.2f} per task (est.)",
                    ],
                    [
                        "Mostly about",
                        ", ".join(f"`{d}` {n}" for d, n in v["top_domains"].items()),
                    ],
                    [
                        "Tools it reaches for",
                        ", ".join(f"`{t}` {n}" for t, n in v["top_tools"].items()),
                    ],
                ],
                ["measure", "value"],
            )
        )
        A("")
        if ex:
            A("**How it was actually asked:**")
            A("")
            for e in ex:
                A(f"> {e}")
                A("")
        A("")

    # ---------------------------------------------------------------- sizing
    A("---")
    A("")
    A("## 6. What this implies for the machine")
    A("")
    A(
        "**The number that sizes a deployment is the prompt the model actually "
        "attends over on each request** — the system prompt, the whole "
        "conversation so far, every file and command result still in context. "
        "That is measured directly from the billing records of every individual "
        "API call, and it is large:"
    )
    A("")
    reqs = _request_sizes(cache_dir) if cache_dir else []
    if reqs:
        srt = sorted(reqs)
        A(
            _tbl(
                [
                    [
                        "median request",
                        f"{_pctl(srt, 50):,}",
                        "The typical working prompt",
                    ],
                    ["p75", f"{_pctl(srt, 75):,}", ""],
                    [
                        "p90",
                        f"{_pctl(srt, 90):,}",
                        "What a deployment must serve routinely",
                    ],
                    ["p99", f"{_pctl(srt, 99):,}", ""],
                    [
                        "**largest single request**",
                        f"**{srt[-1]:,}**",
                        "The ceiling a context window has to clear",
                    ],
                ],
                ["", "prompt tokens", "meaning"],
                "lrl",
            )
        )
        A("")
        A(
            f"_Measured across **{len(srt):,} individual API requests**. Prompt size is "
            "`input + cache_read + cache_write` — every token the model had to attend "
            "over, however it was billed. **These are exact, not estimates.**_"
        )
        A("")
        A(
            "> **This corrects an earlier version of this report,** which sized the "
            "machine from the text tool calls returned. That proxy counts only tool "
            "output — not the conversation, the system prompt, or file contents already "
            "held — and it **understated the real requirement by about 4.6x** at the "
            "median. The per-use-case table below is retained because it is still a "
            "valid way to compare use cases *against each other*, but its absolute "
            "numbers must not be used for sizing. The table above is what sizes hardware."
        )
        A("")
    A("### Relative context pressure, by use case")
    A("")
    A(
        "**Comparative only — see the correction above.** This counts text returned "
        "by tools, which is the part attributable to a specific use case. It shows "
        "which kinds of work pull the most material into context; it does not give "
        "the absolute size of the prompt."
    )
    A("")
    big = sorted(P.items(), key=lambda kv: -kv[1]["est_peak_ctx_tokens"])[:8]
    A(
        _tbl(
            [
                [
                    f"**{v['label']}**",
                    _k(v["median_task_chars"] / uc.CHARS_PER_TOKEN),
                    _k(v["p90_task_chars"] / uc.CHARS_PER_TOKEN),
                    _k(v["est_peak_ctx_tokens"]),
                    _k(v["max_result_chars"] / uc.CHARS_PER_TOKEN),
                ]
                for k, v in big
                if k not in ("unclassified", "boilerplate")
            ],
            [
                "use case",
                "typical task",
                "heavy task (p90)",
                "worst task",
                "worst single result",
            ],
            "lrrrr",
        )
    )
    A("")
    A(
        f"_Token figures converted from characters at {uc.CHARS_PER_TOKEN} "
        "characters per token — an approximation, so treat these as the right "
        "order of magnitude rather than exact. They also count text the agent "
        "read, not the full prompt, so a real context window must be larger._"
    )
    A("")
    A("**What follows from this:**")
    A("")
    if reqs:
        srt = sorted(reqs)
        A(
            f"- **A context window of {_pctl(srt, 90)//1000:,}K tokens serves 9 requests "
            f"in 10.** Below roughly {_pctl(srt, 50)//1000:,}K, the median request does not "
            "fit at all, and the agent is dropping material on ordinary work rather "
            "than only on the hard cases."
        )
        A(
            f"- **The ceiling is {srt[-1]//1000:,}K tokens.** Serving every observed "
            "request without truncation requires a window at that scale; anything "
            "smaller is a decision about which work to decline, and should be made "
            "deliberately."
        )
    A(
        "- The worst single tool result is large enough to consume a substantial "
        "share of a small context on its own, so **truncating tool output is not "
        "optional** — it is a correctness requirement, and where it truncates "
        "determines what the agent can still do."
    )
    A(
        "- Delegation exists to keep this bounded: work handed to a subagent "
        "costs its own context, and only the summary returns to the caller."
    )
    A("")

    # ------------------------------------------------------- system requirements
    A("---")
    A("")
    A("## 7. System requirements — what it takes to run this")
    A("")
    A(
        "Everything above describes the work. This section measures what serving "
        "that work demands of a machine: which way the hardware is stressed, how "
        "many agents run at once, and how much memory their context costs. Each "
        "subsection states its own derivation."
    )
    A("")
    if systems_md:
        # Demote systems.py's own H2s so they nest under this section rather than
        # competing with the report's top-level numbering.
        A(
            re.sub(r"^## ", "### 7.", systems_md, flags=re.M).replace(
                "### 7.", "### 7.", 1
            )
        )
    else:
        A(
            "_Not generated. Run `python -m gaia.factory.harvest.systems "
            "--cache <dir>` and pass its output in; this report does not "
            "estimate these figures when the measurement is absent._"
        )
    A("")

    # ------------------------------------------------------------ recommendation
    A("---")
    A("")
    A("## 8. A recommended architecture, and why")
    A("")
    A(
        "What follows is a design derived from the measurements above. Each "
        "recommendation names the finding that forces it, so a reader who "
        "disagrees with the conclusion can go and check the number."
    )
    A("")
    A(ARCHITECTURE)
    A("")

    # ---------------------------------------------------------------- inventory
    A("---")
    A("")
    A("## 9. What the work required — tool inventory")
    A("")
    A(
        f"**{cov['agent_tools']}** tools built into the agent · "
        f"**{cov['mcp_tools']}** tools from **{cov['mcp_servers']}** external "
        f"servers · **{cov['shell_binaries']:,}** distinct shell programs across "
        f"**{cov['shell_switch_pairs']:,}** distinct program-and-flag combinations."
    )
    A("")
    A(
        f"The ten most-used programs account for **{cov['top10_share']:.0%}** of all "
        f"shell activity, and **{cov['binaries_used_once']:,} programs were used "
        f"exactly once**. That long tail is the part a top-ten list hides, and it "
        "is what decides whether a restricted shell is usable: an allow-list built "
        "from the common cases would block a large number of real, one-off needs."
    )
    A("")
    A(
        _tbl(
            [
                [
                    f"`{r.name}`",
                    f"{r.calls:,}",
                    f"{r.failure_rate:.0%}",
                    ", ".join(f"`{f}`" for f, _ in r.switches.most_common(6)) or "—",
                ]
                for r in inventory["shell"][:20]
            ],
            ["program", "calls", "fail", "flags actually used"],
            "lrrl",
        )
    )
    A("")
    A("**Tools built into the agent, and tools from external servers**")
    A("")
    A(
        _tbl(
            [
                [
                    f"`{r.server+'/' if r.server else ''}{r.name.split('__')[-1]}`",
                    r.surface,
                    f"{r.calls:,}",
                    f"{r.failure_rate:.0%}",
                ]
                for r in (inventory["agent"] + inventory["mcp"])[:20]
            ],
            ["tool", "where it comes from", "calls", "fail"],
            "llrr",
        )
    )
    A("")
    A(
        "_`agent` means the tool ships with the agent. `mcp` means it comes from "
        "an external server the agent connects to — a separate dependency that "
        "must be installed, authenticated and kept running._"
    )
    A("")

    # ---------------------------------------------------------------- limits
    A("---")
    A("")
    A("## 10. Method, and what these numbers cannot tell you")
    A("")
    A(
        "**Stated plainly, because several of these materially limit what can be "
        "concluded.**"
    )
    A("")
    A(
        _tbl(
            [
                [
                    "Token figures per use case are **estimates**",
                    "Billing is per session; a session holds several tasks. "
                    "Session totals are split across tasks in proportion to tool "
                    "calls. A task that burned context without calling tools is "
                    "under-counted. Corpus totals are exact.",
                ],
                [
                    "Context figures are **approximations**",
                    f"Derived from characters returned by tools, at "
                    f"{uc.CHARS_PER_TOKEN} chars per token, and counting only "
                    "tool output — not the system prompt, conversation history or "
                    "the agent's own reasoning. Real context use is higher.",
                ],
                [
                    "Labels come from **keyword patterns, not judgement**",
                    "Deterministic and reproducible, but blunt. A compound ask "
                    "gets one label. Precision was chosen over subtlety so that a "
                    "rerun on another corpus yields comparable numbers.",
                ],
                [
                    "**One corpus, one operator, one subject**",
                    f"`agent_self` is the largest domain because this is largely "
                    "an agent being used to build an agent. The *distribution* "
                    "here is specific to that; the *inventory of what each use "
                    "case requires* generalises better.",
                ],
                [
                    "The shell inventory has a **noisy long tail**",
                    "Shell text cannot be perfectly separated from quoted strings "
                    "and embedded scripts. Frequently-used programs are reliable; "
                    "the single-use tail mixes genuine one-off tools with "
                    "fragments. Counts below ~10 uses should not be trusted "
                    "individually.",
                ],
                [
                    "**No timing data anywhere**",
                    "Neither steps nor requests record how long they took. So "
                    "nothing here sizes for a latency target, and the split "
                    "between local tool execution and accelerator inference "
                    "cannot be computed — only its shape (§7.7). **This is "
                    "the highest-value missing measurement**; adding per-step "
                    "start/end timestamps would close it.",
                ],
                [
                    "Per-use-case context figures are a **proxy**",
                    "They count text returned by tools, which understates the "
                    "real prompt by ~4.6x (§6). Use them to compare use "
                    "cases against each other; use the measured request-size "
                    "table to size hardware.",
                ],
                [
                    "The tiering in §8.3 is a **proposal, not a result**",
                    "94% of this corpus ran on one frontier model. The analysis "
                    "shows which work is short and read-mostly; it does not show "
                    "that a smaller model succeeds at it. That requires an "
                    "evaluation.",
                ],
                [
                    "Small categories are **directional**",
                    "Any use case under ~20 tasks has wide error bars on every "
                    "derived percentage. They are listed for completeness, not "
                    "for planning.",
                ],
            ],
            ["limitation", "what it means for the numbers"],
        )
    )
    A("")
    A(
        "**Reproducing:** every figure comes from the frozen corpus in `_data/`. "
        "Regenerate with "
        "`python -m gaia.factory.harvest.usecase_report --cache _data > report.md`."
    )
    A("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, required=True)
    ap.add_argument(
        "--projects",
        type=Path,
        default=Path.home() / ".claude" / "projects",
        help="Raw transcript root, read once to measure inference concurrency.",
    )
    a = ap.parse_args()
    from gaia.factory.harvest import systems

    traces = systems.load_traces(a.cache)
    load = systems.collect_inference(a.cache, a.projects, traces)
    systems_md = systems.build(traces, load, systems.load_requests(a.cache))
    print(build(traces, a.cache, systems_md))


if __name__ == "__main__":
    main()
