# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Named use cases — the specific job someone wanted done, per task.

:mod:`tasks` answers two general questions: what *kind* of work is this
(activity) and what was it *about* (domain). Both are deliberately abstract, so
that ``implement`` means the same thing whether the subject is a parser or a
policy document. That abstraction is what makes them comparable, and it is also
what makes them unusable on their own for deciding what an agent must support:
``author`` + ``docs`` covers writing a README, fact-checking a specification and
building a slide deck, and those need different tools.

This module adds the concrete layer underneath. Each use case names a recognised
job, carries a plain-language definition, and states whether it is software
delivery or knowledge work. Every entry is derived from the corpus rather than
imagined: the older taxonomy in ``labels.txt`` contributed the software-delivery
names, and the knowledge-work half comes from reading asks the old taxonomy had
no label for — meeting-transcript work, deck building, cost analysis.

Two things distinguish this from the label file it replaces:

* **Per task, not per session.** ``labels.txt`` held one label for each of 280
  sessions, so a session that began as a code review and ended as a release was
  counted as whichever came first.
* **Complete.** A label file covers the sessions someone labelled. Patterns
  cover everything, and what they cannot place is visible as ``unclassified``
  rather than silently absent.

Classification is deterministic keyword matching plus the behaviour override
from :mod:`tasks` — what the agent did outranks what the ask said. No LLM, so a
rerun on another corpus produces comparable labels rather than fresh judgement.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence

from gaia.factory.harvest.tasks import Task

#: The families the use cases fall into, each with the definition a reader needs.
#: These are not a prior taxonomy imposed on the data — they are the groupings
#: that emerged once every task carried a named use case.
TRACKS: Dict[str, str] = {
    "build": "Producing or changing software — new capability, defect repair, "
    "restructuring, dependency moves.",
    "verify": "Establishing whether something is correct — reviewing a change, "
    "auditing a document, writing tests, running evaluations. "
    "Produces findings, not artefacts.",
    "operate": "Moving work through the system and keeping it running — "
    "pipelines, releases, repository mechanics, machine setup.",
    "content": "Producing prose and visuals for people to read — documents, "
    "decks, meeting records.",
    "analyse": "Turning a body of data into an interpretation, or moving it "
    "between shapes.",
    "decide": "Working out what should happen next — research, planning, "
    "triaging what matters, answering questions.",
    "agent": "Work on the agent system itself: its prompts, skills, tools and "
    "orchestration. The corpus is an agent being used to build an agent.",
}


@dataclass(frozen=True)
class UseCase:
    """One recognised job, with the definition a reader needs to interpret it."""

    key: str
    label: str
    #: Which family of work this belongs to. See :data:`TRACKS` — seven, named
    #: for the groupings the corpus produced rather than a two-way split.
    track: str
    #: Plain language, no jargon — this is what gets printed in the glossary.
    definition: str
    pattern: str

    @property
    def evaluates(self) -> str:
        """What a test of this use case must present, and what counts as success.

        This is the field that makes the taxonomy actionable rather than
        descriptive: a category nobody can state a test for is a category that
        cannot be measured, and should not be in the list.
        """
        return EVALUATES[self.key]


#: Ordered — **first match wins**, so the more specific precede the more general.
#: This matters because real asks are compound: "refactor the loader, fix the
#: failing tests and push the PR" legitimately touches three categories, and a
#: single label has to pick one. The rule is that the most precise verb wins, so
#: ``refactor`` is placed ahead of ``bug_fix`` and ``pr_triage`` — "refactor"
#: names one job, whereas "fix" and "PR" appear in asks about almost anything.
#: Placed the other way round, 32 of 35 refactoring asks were absorbed by those
#: two categories.
USE_CASES: Sequence[UseCase] = (
    # --- software delivery ---------------------------------------------------
    UseCase(
        "pr_triage",
        "Pull-request triage",
        "operate",
        "Working through open pull requests to find which are blocked and on "
        "what — unresolved review comments, failing checks, merge conflicts — "
        "and clearing them. The unit of work is the queue, not one change.",
        r"outstanding prs?\b|open prs?\b|my prs?\b|all (my |the )?prs?\b|"
        r"prs? (need|that need|status)|merge conflict|unresolved comment|"
        r"address(ing)? (the )?(pr )?comments|rebase to head of main",
    ),
    UseCase(
        "ci_failure_triage",
        "CI failure triage",
        "operate",
        "A build or test run has gone red and the job is to find out why and "
        "make it green again. Distinct from ordinary debugging because the "
        "failure is reported by automation, often with no human describing the "
        "symptom.",
        r"ci (is|are) fail|failing on your pr|\bci/cd\b.{0,30}fail|"
        r"pipeline fail|build (is )?fail|workflow fail|red build|tests? (are )?failing",
    ),
    UseCase(
        "code_review",
        "Code review",
        "verify",
        "Reading a change someone proposes and judging whether it is correct, "
        "safe and consistent with the codebase. Produces findings, not edits.",
        r"\bcode review\b|review (the |this )?(pr|diff|change|patch|branch)|"
        r"review (the )?code|critique (the )?(code|change|pr)",
    ),
    UseCase(
        "refactor",
        "Refactor",
        "build",
        "Changing how code is organised without changing what it does — "
        "simplifying, splitting, renaming, removing duplication.",
        # `rename` is deliberately absent. It matched the orchestrator's own
        # `claudia_rename_task` boilerplate and made 80% of this category an
        # artefact. Origin filtering in `tasks` removes those prompts, and this
        # keeps the pattern from reintroducing them.
        r"\brefactor\b|clean ?up (the )?code|\btidy\b|simplify (the )?(code|logic)|"
        r"restructure|deduplicate|extract (a |the )?(method|function|class)",
    ),
    UseCase(
        "security_fix",
        "Security fix",
        "build",
        "Finding or closing a vulnerability — leaked credentials, injection, "
        "unsafe permissions, an exposed endpoint.",
        r"vulnerab|\bcve\b|injection|\bsecret(s)?\b|credential leak|"
        r"security (fix|issue|review|concern)|hardcoded (key|token|password)|"
        r"exposed (key|token|endpoint)",
    ),
    UseCase(
        "bug_fix",
        "Bug fix",
        "build",
        "A defect in shipped behaviour is reported and repaired. The symptom "
        "comes from a person using the thing, not from a failing check.",
        r"\bbug\b|\bregression\b|\bbroken\b|not working|doesn'?t work|"
        r"\bfix (the |this )?(issue|bug|error|crash|problem)|\bcrash",
    ),
    UseCase(
        "feature_impl",
        "Feature implementation",
        "build",
        "Building something that did not exist before — a new command, tool, "
        "endpoint or capability.",
        r"\bimplement\b|\badd (a |the |support)\b|\bbuild (a|the|out)\b|"
        r"new (feature|command|tool|agent|endpoint)|\bwire up\b|\bscaffold\b",
    ),
    UseCase(
        "test_authoring",
        "Test authoring",
        "verify",
        "Writing or extending automated tests, and raising coverage. Separate "
        "from running tests to diagnose a failure, which is triage.",
        r"write (a |the |some )?tests?|add (a |unit |integration )?tests?|"
        r"\bpytest\b|test coverage|coverage for|regression test",
    ),
    UseCase(
        "release_cut",
        "Release and packaging",
        "operate",
        "Turning a green main branch into something published — version bump, "
        "changelog, tag, build artefacts, publish job.",
        r"\brelease\b|\bpublish\b|version bump|\bchangelog\b|cut a (patch|release)|"
        r"\btag and\b|ship it|package (it|the)",
    ),
    UseCase(
        "repo_ops",
        "Repository operations",
        "operate",
        "Git and forge mechanics: committing, pushing, branching, rebasing, "
        "merging, closing. Moving work through the system rather than authoring it.",
        r"\bcommit\b|\bpush\b|\bmerge[sd]?\b|\brebase\b|\bbranch\b|\bcherry.?pick\b|"
        r"\bclose (the |this )?(pr|issue)|\bland\b",
    ),
    UseCase(
        "env_setup",
        "Environment and configuration",
        "operate",
        "Getting a machine, service or account into a state where the work can "
        "happen — installing, configuring, authenticating, granting permissions.",
        r"\binstall\b|\bset ?up\b|\bconfigure\b|\bprovision\b|\bpermission\b|"
        r"\bcredential\b|\bapi key\b|\bauth\b|environment variable|\bpath\b.{0,20}\bset",
    ),
    UseCase(
        "dependency_upgrade",
        "Dependency upgrade",
        "operate",
        "Moving to a new version of something the project depends on, and "
        "dealing with what that breaks.",
        r"\bupgrade\b|\bbump (the )?(version|dependency|package)|\bdependabot\b|"
        r"update (the )?(dependency|package|library)",
    ),
    # --- knowledge work ------------------------------------------------------
    UseCase(
        "meeting_transcript",
        "Meeting and transcript work",
        "content",
        "Turning recorded speech into something usable — transcribing, "
        "attributing lines to speakers, then summarising decisions and actions.",
        r"\btranscript\b|\bdiariz|\btranscrib|\bpyannote\b|\bminutes\b|"
        r"speaker (label|attribution)|"
        # "meeting" on its own also matches someone troubleshooting their call
        # audio, which is not transcript work. Pair it with a word that implies
        # producing a record.
        r"\b(meeting|recording|call)\b.{0,40}\b(transcri|summar|note|record|minute)",
    ),
    UseCase(
        "slide_deck",
        "Presentation building",
        "content",
        "Producing a deck: choosing what to show, writing the slides, and "
        "fixing how they render. Heavily visual, and verified by looking.",
        r"\bslides?\b|\bdeck\b|\bpowerpoint\b|\bpptx?\b|\bpresentation\b|"
        r"\bkeynote\b|slide \d",
    ),
    UseCase(
        "doc_authoring",
        "Document authoring",
        "content",
        "Writing prose for people to read — a guide, README, specification, "
        "proposal, report or article.",
        r"\bwrite (a |the |an |up )?(doc|guide|readme|report|proposal|article|"
        r"summary|markdown|spec)|\bdraft\b|\bdocument(ation)? (this|it|the)|"
        r"\bwrite it up\b|\bwhitepaper\b|\bblog\b",
    ),
    UseCase(
        "doc_audit",
        "Document audit and fact-check",
        "verify",
        "Checking existing writing against reality — does the documentation "
        "match the code, are the numbers right, is anything stale or "
        "contradictory. Produces findings, not prose.",
        r"fact.?check|\baudit\b.{0,30}\b(doc|report|readme|claim|number)|"
        r"verif(y|ication).{0,30}\b(doc|claim|number|report)|"
        r"\b(docs?|documentation) (is|are) (wrong|stale|out of date)|"
        r"read.?only (verification|audit)|cross.?check",
    ),
    UseCase(
        "data_analysis",
        "Data and cost analysis",
        "analyse",
        "Taking a body of numbers and producing an interpretation — usage, "
        "spend, performance, benchmark results. The output is a finding with "
        "evidence, not a transformed file.",
        r"\banalys|\banalyz|\bcost\b|\bspend\b|\btoken (usage|cost|econom)|"
        r"\bmetrics?\b|\bstatistic|\bbreakdown\b|how (much|many).{0,25}(cost|token|call)|"
        r"\bdive deep(er)?\b",
    ),
    UseCase(
        "data_extraction",
        "Extraction and conversion",
        "analyse",
        "Getting structured data out of unstructured input, or moving it "
        "between formats — parsing, scraping, converting, tabulating.",
        r"\bextract\b|\bparse\b|\bscrape\b|\bconvert\b.{0,25}\bto\b|"
        r"\btabulate\b|\bpull out\b|into (a )?(csv|json|table|spreadsheet)",
    ),
    UseCase(
        "web_research",
        "External research",
        "decide",
        "Going outside the codebase for information — documentation, articles, "
        "vendor pricing, prior art — and reporting what was found.",
        r"\bresearch\b|look up|\bfind out\b|\binvestigate\b|read this (article|page|url)|"
        r"\bsearch (the web|online|for)\b|state of the art|what'?s out there|"
        r"\bcompare\b.{0,30}\b(option|vendor|tool|approach)",
    ),
    UseCase(
        "planning",
        "Planning and design",
        "decide",
        "Deciding what to do before doing it — roadmaps, milestones, design "
        "options, trade-offs, scoping. Open-ended by nature.",
        r"\bplan\b|\broadmap\b|\bmilestone\b|\bdesign\b|\bproposal\b|\bstrateg|"
        r"\bshould we\b|\boptions? for\b|\btrade.?off|\bscope\b|\bbrainstorm\b|"
        r"\bthink through\b|\bapproach\b",
    ),
    UseCase(
        "issue_triage",
        "Issue triage",
        "decide",
        "Working the issue tracker — reading what is open, deciding what "
        "matters, filing, labelling, closing what is already done.",
        r"\bissues?\b|\bbacklog\b|\bpriorit|\bp0\b|\bp1\b|\bmilestone\b|"
        r"\btriage\b|github issue|#\d{3,}",
    ),
    UseCase(
        "eval_benchmark",
        "Evaluation and benchmarking",
        "verify",
        "Measuring how well a system performs against a defined set of cases, "
        "and comparing runs.",
        r"\beval\b|\bevaluation\b|\bbenchmark\b|\bbaseline\b|\bscorecard\b|"
        r"\bground truth\b|\bjudge\b|\bscore\b.{0,20}\b(against|compare)",
    ),
    UseCase(
        "agent_config",
        "Agent and harness configuration",
        "agent",
        "Working on the agent system itself — its prompts, skills, tools, "
        "memory and orchestration. Using the agent to build the agent.",
        r"\bsystem prompt\b|\bskill\b|\bsubagent\b|\bharness\b|\bclaude ?code\b|"
        r"\bmcp\b|\bagent (config|setup|memory|loop|tool)|\bclaudia\b|\btool call",
    ),
    UseCase(
        "qa_conversational",
        "Question answering",
        "decide",
        "A direct question answered from what the agent already knows or can "
        "quickly look up. Short, and often no tool is used at all.",
        r"^\s*(what|who|when|where|which|why|how|is|are|can|should|does|do|did|"
        r"was|were|will|would)\b|\?\s*$",
    ),
)


#: What a test of each use case must present, and what counts as success.
#: Kept beside the taxonomy so that adding a category forces stating how it
#: would be measured.
EVALUATES: Dict[str, str] = {
    "ci_failure_triage": (
        "Given a failing run's logs, locate the cause and produce a change "
        "that makes it pass. Success is a green re-run, not a plausible "
        "explanation."
    ),
    "pr_triage": (
        "Given several open changes, report which are blocked and why, "
        "without inventing state. Success is a status list that matches the "
        "forge."
    ),
    "code_review": (
        "Given a diff, report the real defects and no invented ones. Scored "
        "on recall and false-positive rate together — a review that flags "
        "everything is worthless."
    ),
    "security_fix": (
        "Given code with a planted weakness, find it and close it without "
        "breaking behaviour. Success requires the fix, not the observation."
    ),
    "bug_fix": (
        "Given a reproducible defect, repair it. Success is the failing case "
        "passing and the rest of the suite still green."
    ),
    "feature_impl": (
        "Given a specification, produce working code that meets it. Scored on "
        "whether it runs, whether it does what was asked, and code quality."
    ),
    "test_authoring": (
        "Given untested code, write tests that exercise it. Success requires "
        "the tests to fail when the code is broken — coverage alone proves "
        "nothing."
    ),
    "refactor": (
        "Given working code, restructure it with behaviour unchanged. Success "
        "is the existing suite passing plus a measurable structural "
        "improvement."
    ),
    "release_cut": (
        "Given a ready branch, execute the release steps in order. Success is "
        "a correct, complete sequence — a procedural-compliance test."
    ),
    "repo_ops": (
        "Given a version-control goal, reach it without data loss. Success is "
        "the intended end state with no destructive side effects."
    ),
    "env_setup": (
        "Given a cold machine, reach a working state. Success is the target "
        "command running — the use case where leftover state most often fakes "
        "a pass."
    ),
    "dependency_upgrade": (
        "Given a version bump, resolve what it breaks. Success is the suite "
        "green on the new version."
    ),
    "meeting_transcript": (
        "Given recorded speech, produce an accurate record and a summary. "
        "Scored on speaker-attribution accuracy and whether decisions and "
        "actions survive."
    ),
    "slide_deck": (
        "Given content and a format, produce a deck that renders correctly. "
        "Requires visual verification — text overflowing its box passes every "
        "textual check."
    ),
    "doc_authoring": (
        "Given a subject, produce prose that is accurate, complete and "
        "readable. Requires a quality judgement, not a diff comparison."
    ),
    "doc_audit": (
        "Given a document and a source of truth, find every claim that "
        "disagrees. Scored on catching real contradictions without "
        "manufacturing them."
    ),
    "data_analysis": (
        "Given data, produce a defensible interpretation. Every number in the "
        "output must be reproducible from the input — this is where agents "
        "fabricate most."
    ),
    "data_extraction": (
        "Given unstructured input, produce the requested structure. Success "
        "is an exact-match comparison against expected output."
    ),
    "web_research": (
        "Given a question needing outside information, find and report it "
        "with sources. Scored on whether the claims survive checking against "
        "what was cited."
    ),
    "planning": (
        "Given a goal and constraints, produce a workable plan. Open-ended, "
        "so scored by rubric — does it address the constraints, are steps "
        "ordered, are risks named."
    ),
    "issue_triage": (
        "Given a backlog, decide what matters and act. Scored on judgement "
        "against a reference triage, not on volume processed."
    ),
    "eval_benchmark": (
        "Given a system and a set of cases, measure it and report honestly. "
        "Success includes reporting a regression rather than explaining it "
        "away."
    ),
    "agent_config": (
        "Given a desired agent behaviour, change prompts, skills or tools to "
        "produce it. Success is the behaviour change being observable "
        "afterwards."
    ),
    "qa_conversational": (
        "Given a direct question, answer it correctly and briefly. Success is "
        "accuracy plus knowing when the honest answer is 'I don't know'."
    ),
}

_COMPILED = [(u, re.compile(u.pattern, re.I)) for u in USE_CASES]
BY_KEY: Dict[str, UseCase] = {u.key: u for u in USE_CASES}


#: Which use case an activity implies when the wording matched nothing. Derived
#: from the dominant pairing observed in the corpus, so an unmatched ask is
#: placed by what it did rather than dropped.
_ACTIVITY_FALLBACK: Dict[str, str] = {
    "review": "code_review",
    "debug": "bug_fix",
    "implement": "feature_impl",
    "author": "doc_authoring",
    "answer": "qa_conversational",
    "research": "web_research",
    "test": "test_authoring",
    "release": "release_cut",
    "operate": "repo_ops",
    "refactor": "refactor",
    "extract": "data_extraction",
    "configure": "env_setup",
}


def classify(task: Task) -> str:
    """The named use case for one task.

    Wording first, then two corrections that matter more than they look:

    * an **automation-triggered** ask about a failing check is CI triage
      whatever its wording, because the alert text varies and the job does not;
    * a **read-only** task that matched an implementation pattern is a review —
      the same behaviour override :mod:`tasks` applies, restated here because a
      use case can be wrong in that direction independently of the activity.
    """
    if task.origin == "boilerplate":
        return "boilerplate"

    text = task.ask
    hit = next((u.key for u, rx in _COMPILED if rx.search(text)), "")

    # An alert about a red pipeline is CI triage even when the body reads like a
    # bug report, which it usually does.
    if task.origin == "automation" and re.search(
        r"\bci\b|\bpipeline\b|\bworkflow\b|\bfail", text, re.I
    ):
        return "ci_failure_triage"

    if not hit:
        return _ACTIVITY_FALLBACK.get(task.activity, "unclassified")

    # Claimed to be research but never went outside and rewrote files instead.
    # `tasks.classify` already applies this correction to the activity; without
    # the same rule here the two disagree — the activity says `implement` while
    # the use case still says research.
    if hit == "web_research" and task.mutated and not task.families.get("web", 0):
        return "feature_impl" if task.activity == "implement" else "code_review"

    # Matched an authoring or implementing pattern but changed nothing and only
    # read: that is review or a question, not production.
    if not task.mutated and task.n_steps > 0:
        fams = task.families
        looked = fams.get("read", 0) + fams.get("search", 0) + fams.get("shell", 0)
        if hit in ("feature_impl", "bug_fix", "refactor", "test_authoring") and looked:
            return "code_review"
        if hit == "doc_authoring" and looked:
            return "doc_audit"
    return hit


def label_all(tasks: Sequence[Task]) -> None:
    """Stamp ``use_case`` on every task, in place."""
    for t in tasks:
        t.use_case = classify(t)


def examples(
    tasks: Sequence[Task], key: str, n: int = 3, width: int = 110
) -> List[str]:
    """Real asks for one use case — the evidence behind the label.

    Prefers asks the pattern matched directly over ones placed by fallback, and
    prefers mid-length asks: a three-word follow-up shows nothing, and a
    thousand-word brief does not fit in a table.
    """
    own = BY_KEY.get(key)
    rx = re.compile(own.pattern, re.I) if own else None
    pool = [
        t
        for t in tasks
        if t.use_case == key and t.origin == "human" and not t.inherited
    ]
    # An ask placed by activity fallback never contained a word for this use
    # case, so quoting it as an example of the category is misleading. Prefer
    # asks the pattern matched on its own wording, and only fall back to the
    # rest if there are none.
    matched = [t for t in pool if rx and rx.search(t.ask)] or pool
    matched.sort(key=lambda t: abs(len(t.ask) - 95))
    return [t.ask[:width] for t in matched[:n]]


def _pct(n: int, d: int) -> float:
    return 100.0 * n / d if d else 0.0


def _quantile(sorted_vals: List[int], q: float) -> int:
    if not sorted_vals:
        return 0
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


#: Characters per token. The corpus records result sizes in characters, and
#: sizing a context window needs tokens. Four is the usual English-and-code
#: approximation; it is an approximation, and every figure derived from it is
#: labelled as an estimate wherever it is printed.
CHARS_PER_TOKEN = 4


def profile(tasks: Sequence[Task], traces: Sequence[dict]) -> Dict[str, dict]:
    """Everything measurable about each use case, in one pass.

    Four groups of fact, because they answer four different questions:

    * **how common** — task and step share, so effort can be prioritised;
    * **how big** — steps and ingested text per task, which is what sizes a
      context window and therefore the memory a model needs;
    * **how it behaves** — mutation rate, failure rate, automation share;
    * **what it costs** — tokens and money.

    Token figures are **apportioned, not measured per task.** Billing is recorded
    once per session and a session holds several tasks, so a session's tokens are
    split across its tasks in proportion to tool calls. A task that burned
    context without calling tools is therefore under-counted. The corpus-wide
    total is exact; every per-use-case token figure is an estimate.
    """
    # Session token totals, and the step count to divide them by.
    per_session: Dict[str, dict] = {}
    for tr in traces:
        u = tr.get("usage") or {}
        per_session[tr.get("session_id", "")] = {
            "total": u.get("total", 0),
            "output": u.get("output_tokens", 0),
            "cache_read": u.get("cache_read_tokens", 0),
            "steps": max(1, len(tr.get("steps") or [])),
            "subagents": len(tr.get("subagents") or []),
            "interrupts": tr.get("interrupts", 0) or 0,
        }

    by: Dict[str, List[Task]] = defaultdict(list)
    for t in tasks:
        by[t.use_case].append(t)

    all_tasks = len(tasks)
    all_steps = sum(t.n_steps for t in tasks) or 1
    grand_tokens = sum(v["total"] for v in per_session.values()) or 1

    out: Dict[str, dict] = {}
    for key, group in by.items():
        worked = [t for t in group if t.n_steps]
        steps = sorted(t.n_steps for t in worked)
        n_steps = sum(t.n_steps for t in group)

        tokens = output = cache_read = 0.0
        interrupts = delegated = 0
        for t in group:
            s = per_session.get(t.session_id)
            if not s:
                continue
            share = t.n_steps / s["steps"]
            tokens += s["total"] * share
            output += s["output"] * share
            cache_read += s["cache_read"] * share
            interrupts += s["interrupts"] * share
            delegated += s["subagents"] * share

        # Context pressure: what a single tool result can inject, and what a
        # whole task accumulates. The peak is what a context window must
        # survive; the per-task total is what it must hold.
        results = sorted(
            int(st.get("result_chars") or 0) for t in group for st in t.steps
        )
        per_task_chars = sorted(
            sum(int(st.get("result_chars") or 0) for st in t.steps) for t in worked
        )
        uc = BY_KEY.get(key)

        out[key] = {
            "key": key,
            "label": uc.label if uc else key,
            "track": uc.track if uc else "—",
            "definition": uc.definition if uc else "",
            "evaluates": uc.evaluates if uc else "",
            # how common
            "tasks": len(group),
            "task_pct": _pct(len(group), all_tasks),
            "worked": len(worked),
            "steps": n_steps,
            "step_pct": _pct(n_steps, all_steps),
            # how big
            "median_steps": _quantile(steps, 0.5),
            "p90_steps": _quantile(steps, 0.9),
            "max_steps": steps[-1] if steps else 0,
            "median_result_chars": _quantile(results, 0.5),
            "p99_result_chars": _quantile(results, 0.99),
            "max_result_chars": results[-1] if results else 0,
            "median_task_chars": _quantile(per_task_chars, 0.5),
            "p90_task_chars": _quantile(per_task_chars, 0.9),
            "est_peak_ctx_tokens": (per_task_chars[-1] if per_task_chars else 0)
            // CHARS_PER_TOKEN,
            # how it behaves
            "mutating_pct": _pct(sum(1 for t in group if t.mutated), len(group)),
            "automation_pct": _pct(
                sum(1 for t in group if t.origin == "automation"), len(group)
            ),
            "failure_pct": _pct(sum(t.failures for t in group), n_steps or 1),
            "interrupts_per_task": interrupts / len(group),
            "delegation_per_task": delegated / len(group),
            # what it costs
            "tokens": int(tokens),
            "token_pct": _pct(int(tokens), grand_tokens),
            "output_tokens": int(output),
            "cache_read_pct": _pct(int(cache_read), int(tokens) or 1),
            "tokens_per_task": int(tokens / len(group)),
            # composition
            "top_domains": dict(Counter(t.domain for t in group).most_common(3)),
            "top_tools": dict(
                Counter(
                    tool for t in group for tool, n in t.tools.items() for _ in range(n)
                ).most_common(6)
            ),
            "top_families": dict(
                Counter(
                    fam
                    for t in group
                    for fam, n in t.families.items()
                    for _ in range(n)
                ).most_common(5)
            ),
        }
    return add_complexity(out)


#: The signals that make a use case demanding, and why each one counts. A
#: composite is a construct, not a measurement, so the inputs are named here and
#: printed wherever the rank appears — a reader who disagrees with the weighting
#: can recompute it from the columns.
COMPLEXITY_SIGNALS: Dict[str, str] = {
    "median_steps": "How long a typical task runs. The most direct measure of "
    "how much work the agent has to sustain.",
    "p90_steps": "How bad the tail gets. A use case with a heavy tail needs "
    "headroom even if its median looks tame.",
    "tokens_per_task": "What it costs to serve, which tracks how much material "
    "the agent must take in and reason over.",
    "median_task_chars": "How much text a task pulls into context — the "
    "pressure it puts on the context window.",
    "failure_pct": "How often an action fails. High failure means the work is "
    "hard to get right, not merely long.",
    "delegation_per_task": "How often the work had to be handed to a subagent. "
    "Delegation is what you reach for when one context will not hold the task.",
}


def _rank01(values: Sequence[float]) -> List[float]:
    """Rank-normalise to 0..1. Rank, not raw value, so one heavy-tailed signal
    cannot dominate the composite the way a z-score would."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    for position, i in enumerate(order):
        out[i] = position / max(1, len(values) - 1)
    return out


def add_complexity(profiles: Dict[str, dict]) -> Dict[str, dict]:
    """Score and rank each use case by how demanding it is.

    Each signal in :data:`COMPLEXITY_SIGNALS` is rank-normalised across use
    cases and averaged with equal weight. The result is **ordinal** — it says
    refactoring is more demanding than answering a question, not that it is
    2.3x more demanding.

    Use cases with too few tasks to be reliable are scored and ranked like any
    other but carry ``complexity_reliable = False``, because a median taken over
    a handful of tasks is not a stable input.
    """
    keys = [k for k in profiles if k not in ("unclassified", "boilerplate")]
    if not keys:
        return profiles
    normalised = {
        signal: _rank01([float(profiles[k].get(signal, 0) or 0) for k in keys])
        for signal in COMPLEXITY_SIGNALS
    }
    scored = []
    for i, k in enumerate(keys):
        score = sum(normalised[s][i] for s in COMPLEXITY_SIGNALS) / len(
            COMPLEXITY_SIGNALS
        )
        profiles[k]["complexity"] = round(100 * score, 1)
        profiles[k]["complexity_inputs"] = {
            s: round(normalised[s][i], 2) for s in COMPLEXITY_SIGNALS
        }
        profiles[k]["complexity_reliable"] = profiles[k].get("tasks", 0) >= 20
        scored.append((k, score))
    for rank, (k, _) in enumerate(sorted(scored, key=lambda kv: -kv[1]), start=1):
        profiles[k]["complexity_rank"] = rank
    return profiles


def summarise(tasks: Sequence[Task]) -> Dict[str, dict]:
    """Per-use-case statistics, for the report table and for eval sampling."""
    out: Dict[str, dict] = {}
    by: Dict[str, List[Task]] = defaultdict(list)
    for t in tasks:
        by[t.use_case].append(t)

    for key, group in by.items():
        worked = [t for t in group if t.n_steps]
        steps = sorted(t.n_steps for t in worked)
        uc = BY_KEY.get(key)
        out[key] = {
            "label": uc.label if uc else key,
            "track": uc.track if uc else "—",
            "definition": uc.definition if uc else "",
            "tasks": len(group),
            "worked": len(worked),
            "median_steps": steps[len(steps) // 2] if steps else 0,
            "max_steps": steps[-1] if steps else 0,
            "mutating": sum(1 for t in group if t.mutated),
            "automation": sum(1 for t in group if t.origin == "automation"),
            "steps_total": sum(t.n_steps for t in group),
            "failures": sum(t.failures for t in group),
            "top_domains": dict(Counter(t.domain for t in group).most_common(3)),
            "top_tools": dict(
                Counter(
                    tool for t in group for tool, n in t.tools.items() for _ in range(n)
                ).most_common(6)
            ),
        }
    return out
