# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Split a session into the tasks a human actually asked for, and label them.

A session is not a task. In this corpus 300 sessions carry 1,208 distinct asks,
34% of sessions carry more than one, and the largest carries 147 — yet the
existing use-case label is assigned once per *session*. Everything that follows
from that label inherits the error: a session that starts as a code review and
ends as a release is counted as whichever the labeller saw first.

Two axes, because one conflates two different questions:

* **activity** — the kind of work. Implementing is implementing whether the
  subject is a parser or a policy document.
* **domain** — what the work was about. Research into a Go library and research
  into a vendor's pricing need the same harness and very different tools.

Classification is deterministic: keyword patterns over the ask, then a
**behaviour override** that lets what the agent did outrank what the human
wrote. An ask that says "research the options" but ran no web call and edited
four files was not research. That inversion is borrowed from
``report._subagent_category``, which found it necessary for the same reason.

No LLM. The point is that a rerun on a different corpus produces comparable
labels rather than a fresh set of judgement calls.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- taxonomy

#: What kind of work the ask is. Ordered: the first pattern that matches wins,
#: so the more specific ones come first.
ACTIVITY_PATTERNS: Sequence[Tuple[str, str]] = (
    # Review before debug. A "READ-ONLY VERIFICATION" ask that happens to mention
    # something that failed is a review, and debug's failure words would claim it
    # first if the order were reversed — that mislabelled 147 tasks on the first
    # pass, most of them audits.
    (
        "review",
        r"read.?only (verification|audit)|\breview\b|\baudit\b|critique|"
        r"assess|fact.?check|verif(y|ication)|sanity.?check|look over|"
        r"check (that|whether|if)",
    ),
    # Debug needs a problem being *reported*, not merely the word "error"
    # appearing somewhere in a long brief.
    (
        "debug",
        r"\bdebug\b|why (is|does|did|are|would)|(is|are|isn't|not) working|"
        r"\bbroken\b|troubleshoot|root.?cause|diagnos|still (seeing|failing)|"
        r"\b(fails?|failing|failed|errors?|bugs?)\b.{0,40}\b(when|after|on|in)\b|"
        r"^\s*(ci|build|test)s? (is|are) fail",
    ),
    ("test", r"\btests?\b|\bpytest\b|coverage|regression|reproduce"),
    ("release", r"\brelease\b|\bpublish\b|\bversion bump\b|changelog|\btag\b|ship it"),
    ("refactor", r"refactor|clean ?up|tidy|simplify|idiomatic|restructure|rename"),
    (
        "research",
        r"\bresearch\b|find out|look up|investigate|compare\b|"
        r"what (is|are|does)|how (do|does|es)\b|options for|state of the art",
    ),
    (
        "author",
        r"\bwrite\b|\bdraft\b|\bdocument\b|\bdocs?\b|readme|summar(y|ise|ize)|"
        r"explain|slide|report|proposal|blog|guide",
    ),
    (
        "extract",
        r"extract|mine\b|parse\b|scrape|transcript|convert|transform|"
        r"pull out|distil|tabulate",
    ),
    (
        "configure",
        r"configure|set ?up|install|provision|enable|disable|"
        r"settings?\b|permission|credential",
    ),
    # Short imperatives dominate follow-up turns: "just push the rebased branch",
    # "merged PR". They are real asks and were landing in unclassified.
    (
        "operate",
        r"\brun\b|\bdeploy\b|restart|monitor|status of|kick off|trigger|"
        r"\bpush\b|\bmerge[sd]?\b|\brebase\b|\bcommit\b|\bland\b|\bclose\b",
    ),
    (
        "implement",
        r"\badd\b|\bimplement\b|\bbuild\b|\bcreate\b|\bfix\b|\bmake\b|"
        r"\bsupport\b|\bwire\b|feature",
    ),
    ("answer", r"^\s*(what|who|when|where|which|is|are|can|should|does|do)\b|\?\s*$"),
)

#: What the work was about. Same first-match-wins ordering.
DOMAIN_PATTERNS: Sequence[Tuple[str, str]] = (
    (
        "agent_self",
        r"\bagent\b|\bskill\b|\bprompt\b|claude ?code|subagent|"
        r"\bmcp\b|\bharness\b|\btool call",
    ),
    (
        "infra_ci",
        r"\bci\b|workflow|pipeline|runner|github action|\bbuild\b|"
        r"docker|deploy|installer",
    ),
    (
        "security",
        r"security|vulnerab|\bcve\b|injection|auth|credential|secret|"
        r"permission|sandbox",
    ),
    (
        "docs",
        r"\bdocs?\b|documentation|readme|\.mdx?\b|guide|changelog|slide|"
        r"whitepaper|report",
    ),
    (
        "data",
        r"\bdata\b|dataset|corpus|transcript|\bcsv\b|\bjson\b|metric|"
        r"benchmark|eval\b|statistic",
    ),
    (
        "product",
        r"\broadmap\b|\bplan\b|milestone|\bissue\b|\bpr\b\b|backlog|"
        r"priorit|customer|user story",
    ),
    (
        "comms",
        r"\bemail\b|\bslack\b|\bteams\b|\bmeeting\b|calendar|reply|"
        r"draft a (note|message)",
    ),
    (
        "software",
        r"\bcode\b|\bfunction\b|\bclass\b|\bmodule\b|\bapi\b|\btest\b|"
        r"\.py\b|\.go\b|\.ts\b|refactor|implement",
    ),
)

#: Families that mean the agent changed something rather than looked at it.
_MUTATING = frozenset({"edit", "write"})

#: Not every user-role turn is a human ask. Tooling injects prompts into the same
#: channel, and they were being classified as though a person wrote them: 191 of
#: 1,535 tasks (12%). ``claudia_rename_task`` in a boilerplate nudge matched the
#: ``rename`` pattern, which made 80% of the ``refactor`` activity an artefact of
#: the orchestrator talking to itself.
#:
#: Two kinds, kept apart because they deserve opposite treatment:
#:
#: * ``boilerplate`` — a nudge carrying no request. Not a task; excluded.
#: * ``automation`` — a real unit of work a machine triggered, e.g. a CI-failure
#:   alert. Counted, but tagged, because "how much work arrives without a human
#:   asking" is a finding rather than contamination.
_ORIGIN_PATTERNS: Sequence[Tuple[str, str]] = (
    ("boilerplate", r"^\s*\[CONTEXT UPDATE\b|^\s*Reconnecting after idle\b"),
    ("automation", r"^\s*\[(CLAUDIA|AUTO|SYSTEM|REMINDER)\b|^\s*<system-reminder"),
)


def origin_of(ask: str) -> str:
    """``human``, ``automation`` or ``boilerplate`` for one prompt."""
    for name, pat in _ORIGIN_PATTERNS:
        if re.search(pat, ask, re.I):
            return name
    return "human"


# --------------------------------------------------------------------------- model


@dataclass
class Task:
    """One human ask and everything the agent did about it."""

    session_id: str
    prompt_index: int
    ask: str
    steps: List[dict] = field(default_factory=list)
    activity: str = "unclassified"
    domain: str = "unclassified"
    #: True when the label came from the preceding task rather than this ask's
    #: own words. Kept visible so a table can exclude them — an inherited label
    #: is weaker evidence than one the ask earned.
    inherited: bool = False
    #: Who produced this prompt — ``human``, ``automation`` or ``boilerplate``.
    #: See :data:`_ORIGIN_PATTERNS`.
    origin: str = "human"
    #: The specific named use case, e.g. ``pr_triage``. See :mod:`use_cases`.
    use_case: str = ""

    @property
    def is_work(self) -> bool:
        """Does this represent a real unit of work someone wanted done?

        Boilerplate nudges do not: they carry no request, so counting them
        overstates both task volume and whichever activity their wording
        accidentally matched.
        """
        return self.origin != "boilerplate"

    @property
    def families(self) -> Dict[str, int]:
        return dict(Counter(s.get("family", "other") for s in self.steps))

    @property
    def tools(self) -> Dict[str, int]:
        return dict(Counter(s.get("tool", "unknown") for s in self.steps))

    @property
    def n_steps(self) -> int:
        return len(self.steps)

    @property
    def failures(self) -> int:
        return sum(1 for s in self.steps if s.get("ok") is False)

    @property
    def mutated(self) -> bool:
        """Did this task change anything, or only look?"""
        return any(s.get("family") in _MUTATING for s in self.steps)

    @property
    def flow(self) -> str:
        """The family sequence with runs collapsed — the task's shape.

        ``read read read shell shell edit`` becomes ``read>shell>edit``, which is
        comparable across tasks where the raw sequence is not.
        """
        out: List[str] = []
        for s in self.steps:
            fam = s.get("family", "other")
            if not out or out[-1] != fam:
                out.append(fam)
        return ">".join(out)


# --------------------------------------------------------------------------- segment


def segment(trace: dict) -> List[Task]:
    """Split one trace into its tasks, one per user ask that did any work.

    Asks that drove no tool call are kept with zero steps: a question answered
    from context is a real use of the agent, and dropping them would overstate
    how tool-heavy the corpus is.
    """
    prompts: List[str] = trace.get("prompts") or []
    tasks = [
        Task(
            session_id=trace.get("session_id", ""),
            prompt_index=i,
            ask=" ".join(str(p).split()),
            origin=origin_of(str(p)),
        )
        for i, p in enumerate(prompts)
    ]
    orphans = Task(
        session_id=trace.get("session_id", ""), prompt_index=-1, ask="(before any ask)"
    )
    for step in trace.get("steps") or []:
        idx = step.get("prompt_index", -1)
        if 0 <= idx < len(tasks):
            tasks[idx].steps.append(step)
        else:
            orphans.steps.append(step)
    for t in tasks:
        # A boilerplate nudge has no request to classify. Its wording still
        # matches patterns — "claudia_rename_task" hits `rename` — so it must be
        # excluded before classification rather than filtered afterwards.
        if t.origin == "boilerplate":
            t.activity, t.domain = "boilerplate", "boilerplate"
        else:
            t.activity, t.domain = classify(t)

    # A follow-up turn continues the task before it: "just push the rebased
    # branch", "merged PR", "looks like it's working now". They carry no verb of
    # their own but they are not a different piece of work, and leaving them
    # unclassified understated every activity they belonged to.
    for i, t in enumerate(tasks):
        if t.origin == "boilerplate":
            continue
        if t.activity != "unclassified" and t.domain != "unclassified":
            continue
        for prev in reversed(tasks[:i]):
            if prev.activity in ("unclassified", "boilerplate"):
                continue
            if t.activity == "unclassified":
                t.activity, t.inherited = prev.activity, True
            if t.domain == "unclassified" and prev.domain != "unclassified":
                t.domain, t.inherited = prev.domain, True
            break

    if orphans.steps:
        orphans.activity, orphans.domain = classify(orphans)
        tasks.append(orphans)
    return tasks


# --------------------------------------------------------------------------- classify


def _first_match(text: str, patterns: Sequence[Tuple[str, str]]) -> Optional[str]:
    for name, pat in patterns:
        if re.search(pat, text, re.I):
            return name
    return None


def classify(task: Task) -> Tuple[str, str]:
    """Label a task by what was asked, corrected by what was done.

    The override matters: asks are written by humans in a hurry and routinely
    describe something other than the work that followed. Where the text and the
    behaviour disagree, the behaviour is the evidence.
    """
    text = task.ask
    fams = task.families
    activity = _first_match(text, ACTIVITY_PATTERNS)
    domain = _first_match(text, DOMAIN_PATTERNS)

    # --- behaviour overrides -------------------------------------------------
    web = fams.get("web", 0)
    mutating = sum(fams.get(f, 0) for f in _MUTATING)
    looking = fams.get("read", 0) + fams.get("search", 0)

    # "research this" that never touched the web and rewrote files was not research.
    if activity == "research" and web == 0 and mutating > 0:
        activity = (
            "implement" if not task.ask.lower().startswith("review") else "review"
        )
    # A pure-lookup task that changed nothing is answering, not implementing.
    if activity == "implement" and mutating == 0 and looking > 0 and task.n_steps > 0:
        activity = "research" if web else "answer"
    # An ask with no verb we recognise, but which clearly changed things.
    if activity is None and mutating > 0:
        activity = "implement"
    if activity is None and task.n_steps == 0:
        activity = "answer"

    # Domain fallback follows the tools, since an unlabelled ask that spent its
    # time in the shell on a repo is software work whatever the wording.
    if domain is None:
        if web and not mutating:
            domain = "product"
        elif fams.get("shell", 0) or mutating:
            domain = "software"

    return activity or "unclassified", domain or "unclassified"


# --------------------------------------------------------------------------- rollup


def summarise(tasks: Sequence[Task]) -> Dict[str, object]:
    """Corpus-level counts, for the report tables and for sampling the eval set."""
    grid: Counter = Counter()
    for t in tasks:
        grid[(t.activity, t.domain)] += 1
    worked = [t for t in tasks if t.n_steps]
    return {
        "tasks": len(tasks),
        "tasks_with_tool_calls": len(worked),
        "by_activity": dict(Counter(t.activity for t in tasks).most_common()),
        "by_domain": dict(Counter(t.domain for t in tasks).most_common()),
        "grid": {f"{a}|{d}": n for (a, d), n in grid.most_common()},
        "mutating_tasks": sum(1 for t in tasks if t.mutated),
        "top_flows": dict(Counter(t.flow for t in worked).most_common(15)),
    }
