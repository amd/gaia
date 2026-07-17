# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Adaptive day-zero onboarding: the bootstrap conversation core.

A pure engine — it walks a question graph, proposes memory entries, shows them
for approval, and stores the approved ones through an injected ``MemoryStore``.
It has no LLM dependency and no console dependency: all IO enters as the
injected ``prompt_fn`` / ``output_fn`` callables, so the CLI can drive it over
stdio while an agent drives it over a chat transport.

That injection is what keeps ``gaia memory bootstrap --chat-only`` working on a
machine with no Lemonade Server: the CLI passes a bare ``MemoryStore`` and no
``on_stored`` hook, so no embedding is attempted. Agents pass an ``on_stored``
hook (see ``MemoryMixin.run_bootstrap_conversation``) and get embeddings inline.
The stored rows are identical either way — the CLI path's embeddings are
backfilled at the next agent start.

Adaptivity is rule-based (keyword branching), not LLM-driven — an LLM branch
would re-introduce the Lemonade dependency this flow deliberately avoids.

Spec: docs/spec/agent-memory-architecture.md ("Bootstrap: Day-Zero Onboarding")
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from gaia.agents.base.memory_store import VALID_CATEGORIES, MemoryStore
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Onboarding answers come straight from the user, so they are stored with
#: ``source="user"`` — the source the reset path preserves (spec: "New
#: discoveries don't overwrite user-edited memories") and the dedup merge
#: refuses to downgrade (memory_store.py, ``CASE WHEN source = 'user'``).
BOOTSTRAP_SOURCE: str = "user"

#: Confidence for a fact the user stated about themselves.
BOOTSTRAP_CONFIDENCE: float = 0.8

#: The first question of the flow.
START_ID: str = "name"

#: How many distinct use-case contexts must be detected before onboarding
#: suggests creating them (spec: "Suggests creating contexts").
_MIN_CONTEXTS_FOR_SUGGESTION: int = 2

#: The review question, and the three answers it accepts.
_REVIEW_PROMPT: str = "Store this? [Y/n/q]:"
_APPROVE: str = "approve"
_DECLINE: str = "decline"
_QUIT: str = "quit"

#: How many times to re-ask when the review answer isn't Y, n, or q. After
#: this, the entry is left out — an unparsed answer must never mean "store".
_MAX_REVIEW_ATTEMPTS: int = 3


class BootstrapCancelled(Exception):
    """Raised by a caller's ``prompt_fn`` to abort onboarding.

    The stdio adapter raises it on EOF / Ctrl-C. The engine catches only this
    exception: it stops asking, keeps whatever the user already approved, and
    returns a result with ``cancelled=True``.
    """


@dataclass(frozen=True)
class ProposedEntry:
    """One memory entry proposed by onboarding — the unit of user review.

    Nothing reaches the database until the user approves the entry it came from.
    """

    content: str
    category: str
    context: str = "global"
    entity: Optional[str] = None
    confidence: float = BOOTSTRAP_CONFIDENCE


@dataclass(frozen=True)
class BootstrapQuestion:
    """A node in the onboarding question graph.

    Attributes:
        id: Node id; must match the key it is registered under.
        prompt: The question shown to the user.
        category: Memory category for the entry built from the answer.
        context: Memory context for that entry ("global", "work", ...).
        template: Content template; ``{answer}`` is substituted.
        branches: Keyword pattern -> next node id. Patterns are pipe-separated
            alternatives matched case-insensitively on word boundaries, in
            insertion order (first match wins).
        next_id: Next node when no branch matches (and when the answer is
            skipped). ``None`` ends the flow.
        builder: Optional override that turns an answer into one or more
            entries. Defaults to a single entry from ``template``.
    """

    id: str
    prompt: str
    category: str = "profile"
    context: str = "global"
    template: str = "{answer}"
    branches: Dict[str, str] = field(default_factory=dict)
    next_id: Optional[str] = None
    builder: Optional[Callable[["BootstrapQuestion", str], List[ProposedEntry]]] = None


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of one onboarding run.

    Attributes:
        stored: Rows written to the database. Can be lower than the number of
            approvals: ``store()`` dedups near-identical content within the
            same category/context/entity.
        skipped: Questions the user answered with an empty line.
        rejected: Proposed entries the user declined at the review step.
        unreviewed: Proposed entries never shown, because the user quit the
            review or cancelled. Not stored, not rejected — just unseen.
        cancelled: True when the user aborted (EOF / Ctrl-C). Entries approved
            before the abort are still stored.
        answers: Question id -> the user's raw answer.
        knowledge_ids: Ids of the stored rows, in approval order.
    """

    stored: int
    skipped: int
    rejected: int
    unreviewed: int
    cancelled: bool
    answers: Dict[str, str]
    knowledge_ids: List[str]


# ============================================================================
# Keyword maps — the rule-based adaptivity
# ============================================================================

#: IDE keyword -> (entity id, display name). Ordered: the first match wins, so
#: longer names precede the shorter names they contain.
_ENTITY_MAP: Tuple[Tuple[str, Tuple[str, str]], ...] = (
    ("visual studio code", ("app:vscode", "VS Code")),
    ("vs code", ("app:vscode", "VS Code")),
    ("vscode", ("app:vscode", "VS Code")),
    ("pycharm", ("app:pycharm", "PyCharm")),
    ("intellij", ("app:intellij", "IntelliJ IDEA")),
    ("cursor", ("app:cursor", "Cursor")),
    ("neovim", ("app:neovim", "Neovim")),
    ("vim", ("app:vim", "Vim")),
    ("sublime", ("app:sublime", "Sublime Text")),
    ("emacs", ("app:emacs", "Emacs")),
    ("xcode", ("app:xcode", "Xcode")),
    ("figma", ("app:figma", "Figma")),
)

#: Context -> keywords that imply the user works in it.
_CONTEXT_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "work": ("work", "job", "coding", "professional"),
    "personal": ("personal", "home", "family", "hobby"),
    "learning": ("learning", "study", "school", "course", "university"),
}


def _matches(keyword: str, text: str) -> bool:
    """True when *keyword* occurs in *text* as a whole word (case-insensitive).

    Word boundaries matter: a naive substring match would route "I build
    things" to the designer branch because "build" contains "ui".
    """
    return (
        re.search(rf"\b{re.escape(keyword)}\b", text, flags=re.IGNORECASE) is not None
    )


def _detect_ide(answer: str) -> Tuple[Optional[str], Optional[str]]:
    """Find the first IDE named in *answer*.

    Args:
        answer: The user's raw tools/languages answer.

    Returns:
        ``(entity, display_name)`` for the first IDE matched, else
        ``(None, None)``.
    """
    for keyword, (entity, display) in _ENTITY_MAP:
        if _matches(keyword, answer):
            return entity, display
    return None, None


def _detect_contexts(answer: str) -> List[str]:
    """Return the memory contexts implied by *answer*, in canonical order.

    Args:
        answer: The user's raw use-cases answer.

    Returns:
        A list of context names (subset of ``_CONTEXT_KEYWORDS``), possibly
        empty.
    """
    return [
        context
        for context, keywords in _CONTEXT_KEYWORDS.items()
        if any(_matches(keyword, answer) for keyword in keywords)
    ]


# ============================================================================
# Entry builders
# ============================================================================


def _default_builder(question: BootstrapQuestion, answer: str) -> List[ProposedEntry]:
    """Build the single entry a question proposes by default.

    Args:
        question: The question that was answered.
        answer: The user's raw answer (already stripped, non-empty).

    Returns:
        A one-element list holding the templated entry.
    """
    return [
        ProposedEntry(
            content=question.template.format(answer=answer),
            category=question.category,
            context=question.context,
        )
    ]


def _tools_builder(question: BootstrapQuestion, answer: str) -> List[ProposedEntry]:
    """Build the work-scoped stack facts, plus a global mirror of the stack.

    The spec scopes the stack to ``context="work"`` with an ``entity`` link, and
    emits a dedicated IDE fact alongside it. But a default chat runs in the
    ``global`` context, and the prompt builder selects *only* global rows there
    (``get_by_category_contexts``) — so work-scoped rows alone would never reach
    the system prompt, and the user's stack would go quiet on day one. The
    global ``profile`` mirror is what keeps it visible; the work rows are what
    make it entity-linked and context-scoped.

    Args:
        question: The tools question.
        answer: The user's raw tools/languages answer.

    Returns:
        The global stack entry and the work-scoped stack entry, plus the IDE
        entry when an IDE was recognised.
    """
    entity, display = _detect_ide(answer)
    entries = [
        ProposedEntry(
            content=f"User's primary tools and languages: {answer}",
            category="profile",
            context="global",
        ),
        ProposedEntry(
            content=question.template.format(answer=answer),
            category=question.category,
            context=question.context,
            entity=entity,
        ),
    ]
    if entity is not None:
        entries.append(
            ProposedEntry(
                content=f"Uses {display} as primary IDE",
                category=question.category,
                context=question.context,
                entity=entity,
            )
        )
    return entries


def _use_cases_builder(question: BootstrapQuestion, answer: str) -> List[ProposedEntry]:
    """Build the use-cases fact, plus the contexts the answer implies.

    The second entry is what the spec calls "suggests creating contexts": the
    user sees it in the review list and approves it. It is phrased as a fact
    about the user, not as a note to the assistant, because profile rows are
    injected verbatim into the "User profile:" block of the system prompt.

    Args:
        question: The use-cases question.
        answer: The user's raw use-cases answer.

    Returns:
        One entry, or two when the answer names at least
        ``_MIN_CONTEXTS_FOR_SUGGESTION`` distinct contexts.
    """
    entries = _default_builder(question, answer)
    contexts = _detect_contexts(answer)
    if len(contexts) >= _MIN_CONTEXTS_FOR_SUGGESTION:
        entries.append(
            ProposedEntry(
                content=f"Uses GAIA across these contexts: {', '.join(contexts)}",
                category="profile",
                context="global",
            )
        )
    return entries


# ============================================================================
# The question graph
# ============================================================================

#: Spine: name -> role -> <role follow-up> -> use_cases -> comms_style ->
#: timezone -> tools -> interests -> extra.  The role answer picks the
#: follow-up: a student is asked about coursework, an engineer about their
#: deployment workflow (spec: "The questions are adaptive").
BOOTSTRAP_GRAPH: Dict[str, BootstrapQuestion] = {
    "name": BootstrapQuestion(
        id="name",
        prompt="What's your name?",
        template="User's name is {answer}",
        next_id="role",
    ),
    "role": BootstrapQuestion(
        id="role",
        prompt="What do you do? (role, profession, or student)",
        template="User's role/profession: {answer}",
        branches={
            "student": "coursework",
            "engineer|developer|programmer|software": "eng_workflow",
            "designer|ux|ui": "design_tools",
            "researcher|scientist|phd|academic": "research_area",
            "manager|lead|pm|founder": "team_context",
        },
        next_id="generic_focus",
    ),
    # --- role follow-ups -------------------------------------------------
    "coursework": BootstrapQuestion(
        id="coursework",
        prompt="What are you studying, and how do you like to study?",
        template="User's coursework and study habits: {answer}",
        next_id="use_cases",
    ),
    "eng_workflow": BootstrapQuestion(
        id="eng_workflow",
        prompt="What does your day-to-day engineering workflow look like? "
        "(stack, testing, deployment)",
        category="fact",
        context="work",
        template="User's engineering workflow: {answer}",
        next_id="use_cases",
    ),
    "design_tools": BootstrapQuestion(
        id="design_tools",
        prompt="What does your design process look like? (tools, handoff)",
        category="fact",
        context="work",
        template="User's design process: {answer}",
        next_id="use_cases",
    ),
    "research_area": BootstrapQuestion(
        id="research_area",
        prompt="What's your research area, and what are you working on now?",
        template="User's research area: {answer}",
        next_id="use_cases",
    ),
    "team_context": BootstrapQuestion(
        id="team_context",
        prompt="What does your team build, and how big is it?",
        category="fact",
        context="work",
        template="User's team context: {answer}",
        next_id="use_cases",
    ),
    "generic_focus": BootstrapQuestion(
        id="generic_focus",
        prompt="What kinds of tasks do you most want help with?",
        template="User's main focus areas: {answer}",
        next_id="use_cases",
    ),
    # --- shared spine ----------------------------------------------------
    "use_cases": BootstrapQuestion(
        id="use_cases",
        prompt="What will you mainly use GAIA for?",
        template="User's primary use cases for GAIA: {answer}",
        builder=_use_cases_builder,
        next_id="comms_style",
    ),
    "comms_style": BootstrapQuestion(
        id="comms_style",
        prompt="How should I communicate with you? "
        "(concise/detailed, casual/formal)",
        category="preference",
        template="Preferred communication style: {answer}",
        next_id="timezone",
    ),
    "timezone": BootstrapQuestion(
        id="timezone",
        prompt="What timezone are you in? (e.g. America/Los_Angeles)",
        template="Timezone: {answer}",
        next_id="tools",
    ),
    "tools": BootstrapQuestion(
        id="tools",
        prompt="What programming languages or tools do you use most?",
        category="fact",
        context="work",
        template="Primary stack: {answer}",
        builder=_tools_builder,
        next_id="interests",
    ),
    "interests": BootstrapQuestion(
        id="interests",
        prompt="What are your interests or hobbies outside of work?",
        template="User's interests and hobbies: {answer}",
        next_id="extra",
    ),
    "extra": BootstrapQuestion(
        id="extra",
        prompt="Anything else you'd like me to know about you?",
        template="Additional user context: {answer}",
        next_id=None,
    ),
}


# ============================================================================
# Validation — the store does not validate categories, so we must
# ============================================================================


def _validate_category(category: str, where: str) -> None:
    """Raise if *category* is not a category MemoryStore recognises.

    ``MemoryStore.store()`` accepts any string, so an unknown category would be
    written silently and then never render into a system prompt.

    Args:
        category: The category to check.
        where: Human-readable origin, used in the error message.

    Raises:
        ValueError: If the category is outside ``VALID_CATEGORIES``.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Bootstrap {where} uses category '{category}', which is not a "
            f"MemoryStore category. Valid categories: {sorted(VALID_CATEGORIES)} "
            "(see VALID_CATEGORIES in src/gaia/agents/base/memory_store.py). "
            "MemoryStore.store() does not validate categories, so this row "
            "would be stored and then never recalled — fix the category in "
            "BOOTSTRAP_GRAPH (src/gaia/agents/base/bootstrap.py)."
        )


def _validate_graph(graph: Dict[str, BootstrapQuestion], start: str) -> None:
    """Check a question graph before asking anything.

    Args:
        graph: Node id -> question.
        start: Id of the first question.

    Raises:
        ValueError: If the graph is empty, the start node is missing, a node id
            disagrees with its key, a category is invalid, or an edge points at
            an unknown node.
    """
    if not graph:
        raise ValueError(
            "Bootstrap question graph is empty — pass BOOTSTRAP_GRAPH "
            "(src/gaia/agents/base/bootstrap.py) or a non-empty graph."
        )
    if start not in graph:
        raise ValueError(
            f"Bootstrap start node '{start}' is not in the question graph. "
            f"Known nodes: {sorted(graph)}."
        )
    for node_id, question in graph.items():
        if question.id != node_id:
            raise ValueError(
                f"Bootstrap question registered under '{node_id}' declares "
                f"id='{question.id}'. The key and the id must match."
            )
        _validate_category(question.category, f"question '{node_id}'")
        targets = list(question.branches.values())
        if question.next_id is not None:
            targets.append(question.next_id)
        for target in targets:
            if target not in graph:
                raise ValueError(
                    f"Bootstrap question '{node_id}' points at unknown node "
                    f"'{target}'. Known nodes: {sorted(graph)}."
                )


# ============================================================================
# Engine
# ============================================================================


def next_question_id(question: BootstrapQuestion, answer: str) -> Optional[str]:
    """Pick the next question from *answer* — this is the adaptive branch.

    Public so a step-wise driver (an onboarding wizard in the UI, say) can walk
    the same graph one request at a time instead of re-deriving the branching.

    Args:
        question: The question that was just answered.
        answer: The user's raw answer.

    Returns:
        The next question's id, ``question.next_id`` when no branch matches, or
        ``None`` at the end of the flow.
    """
    for patterns, target in question.branches.items():
        if any(_matches(keyword, answer) for keyword in patterns.split("|")):
            return target
    return question.next_id


def build_entries(question: BootstrapQuestion, answer: str) -> List[ProposedEntry]:
    """Turn one answer into the entries it proposes — nothing is stored here.

    Public for the same reason as ``next_question_id``: a step-wise driver needs
    the proposals to render its own review UI.

    Args:
        question: The question that was answered.
        answer: The user's raw answer (non-empty).

    Returns:
        The proposed entries, each with a category the store recognises.

    Raises:
        ValueError: If a builder emits a category outside ``VALID_CATEGORIES``.
    """
    builder = question.builder or _default_builder
    entries = builder(question, answer)
    for entry in entries:
        _validate_category(entry.category, f"entry from '{question.id}'")
    return entries


def _render(entry: ProposedEntry) -> str:
    """Format a proposed entry for the review list."""
    line = f"[{entry.category}/{entry.context}] {entry.content}"
    if entry.entity:
        line += f" (entity: {entry.entity})"
    return line


def _ask_approval(
    prompt_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> str:
    """Ask whether to store one entry, and insist on an answer we understand.

    Only an explicit yes (or a bare Enter, the documented default) stores the
    entry. An unrecognised reply — "no", "nope", a typo — is re-asked rather
    than taken as approval: treating it as a yes would write data the user was
    trying to decline.

    Args:
        prompt_fn: Asks the user one question and returns the raw reply.
        output_fn: Shows one line of narration to the user.

    Returns:
        ``_APPROVE``, ``_DECLINE``, or ``_QUIT``.

    Raises:
        BootstrapCancelled: Propagated from *prompt_fn* when the user aborts.
    """
    for _ in range(_MAX_REVIEW_ATTEMPTS):
        choice = prompt_fn(_REVIEW_PROMPT).strip().lower()
        if choice in ("", "y", "yes"):
            return _APPROVE
        if choice in ("n", "no"):
            return _DECLINE
        if choice in ("q", "quit"):
            return _QUIT
        output_fn(f"  '{choice}' isn't Y, n, or q — please answer again.")

    output_fn("  Still not Y, n, or q — leaving this one out to be safe.")
    return _DECLINE


def _db_hint(store: MemoryStore) -> str:
    """Return a ' (database: ...)' fragment for error messages, or ''."""
    db_path = getattr(store, "_db_path", None)
    return f" (database: {db_path})" if db_path else ""


def _store_entry(
    store: MemoryStore,
    entry: ProposedEntry,
    on_stored: Optional[Callable[[str, str], None]],
) -> str:
    """Write one approved entry and run the caller's post-store hook.

    Args:
        store: The ``MemoryStore`` to write to.
        entry: The approved entry.
        on_stored: Optional hook called with ``(knowledge_id, content)`` after
            the write — agents use it to embed the new row.

    Returns:
        The knowledge id of the stored entry.

    Raises:
        RuntimeError: If the write fails, or if *on_stored* fails.
    """
    try:
        knowledge_id = store.store(
            category=entry.category,
            content=entry.content,
            context=entry.context,
            entity=entry.entity,
            source=BOOTSTRAP_SOURCE,
            confidence=entry.confidence,
        )
    except Exception as e:
        raise RuntimeError(
            f"Onboarding could not store '{entry.content[:60]}' "
            f"(category={entry.category}, context={entry.context})"
            f"{_db_hint(store)}: {e}. Check that the memory database is "
            "writable and not held by another GAIA process (`gaia kill` "
            "clears stale ones), then re-run `gaia memory bootstrap "
            "--chat-only`."
        ) from e

    if on_stored is not None:
        try:
            on_stored(knowledge_id, entry.content)
        except Exception as e:
            raise RuntimeError(
                f"Onboarding stored entry {knowledge_id} but could not embed "
                f"it: {e}. The row itself is safe and will be embedded at the "
                "next agent start. To finish onboarding now, start "
                "lemonade-server, or run `gaia memory bootstrap --chat-only`, "
                "which needs no embedder."
            ) from e

    logger.debug("[bootstrap] stored %s (%s)", knowledge_id, entry.category)
    return knowledge_id


def run_bootstrap_conversation(
    store: MemoryStore,
    *,
    prompt_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    on_stored: Optional[Callable[[str, str], None]] = None,
    questions: Optional[Dict[str, BootstrapQuestion]] = None,
    start: str = START_ID,
) -> BootstrapResult:
    """Run the adaptive onboarding conversation and store the approved answers.

    Walks the question graph (the role answer selects the follow-up), turns the
    answers into proposed entries, shows every entry for approval, and stores
    only the approved ones with ``source="user"``.

    Args:
        store: An open ``MemoryStore`` to write approved entries to.
        prompt_fn: Asks the user one question and returns the raw reply. Raise
            ``BootstrapCancelled`` from it to abort (the stdio adapter does this
            on EOF / Ctrl-C).
        output_fn: Shows one line of narration to the user.
        on_stored: Optional hook called with ``(knowledge_id, content)`` after
            each write. Pass ``None`` (the CLI does) to store without embedding
            — the rows are embedded at the next agent start.
        questions: Question graph. Defaults to ``BOOTSTRAP_GRAPH``.
        start: Id of the first question. Defaults to ``START_ID``.

    Returns:
        A ``BootstrapResult`` with the stored/skipped/rejected/unreviewed
        counts, whether the user cancelled, the raw answers, and the ids of the
        stored rows.

    Raises:
        ValueError: If the question graph is malformed — unknown category,
            dangling edge, missing start node, or a cycle.
        RuntimeError: If an approved entry cannot be stored or embedded.
    """
    graph = BOOTSTRAP_GRAPH if questions is None else questions
    _validate_graph(graph, start)

    answers: Dict[str, str] = {}
    proposed: List[ProposedEntry] = []
    skipped = 0

    try:
        node_id: Optional[str] = start
        visited: List[str] = []
        while node_id is not None:
            if node_id in visited:
                raise ValueError(
                    f"Bootstrap question graph loops back to '{node_id}' "
                    f"(path: {' -> '.join(visited)}). Onboarding would re-ask "
                    "the same question forever — remove the cycle from the "
                    "branches/next_id edges."
                )
            visited.append(node_id)

            question = graph[node_id]
            answer = prompt_fn(question.prompt).strip()
            if not answer:
                skipped += 1
                node_id = question.next_id
                continue

            answers[question.id] = answer
            proposed.extend(build_entries(question, answer))
            node_id = next_question_id(question, answer)
    except BootstrapCancelled:
        logger.info("[bootstrap] cancelled by the user before the review step")
        return BootstrapResult(
            stored=0,
            skipped=skipped,
            rejected=0,
            unreviewed=len(proposed),
            cancelled=True,
            answers=answers,
            knowledge_ids=[],
        )

    if not proposed:
        output_fn("\nNothing to store — every question was skipped.")
        return BootstrapResult(
            stored=0,
            skipped=skipped,
            rejected=0,
            unreviewed=0,
            cancelled=False,
            answers=answers,
            knowledge_ids=[],
        )

    # Show before store — nothing is written without explicit approval.
    output_fn(f"\nHere's what I learned ({len(proposed)} items):")
    output_fn("  [Y] = store (default)   [n] = skip   [q] = stop review\n")

    knowledge_ids: List[str] = []
    rejected = 0
    unreviewed = 0
    cancelled = False

    for index, entry in enumerate(proposed, 1):
        output_fn(f"  ({index}/{len(proposed)}) {_render(entry)}")
        try:
            choice = _ask_approval(prompt_fn, output_fn)
        except BootstrapCancelled:
            logger.info("[bootstrap] cancelled by the user during review")
            cancelled = True
            unreviewed = len(proposed) - index + 1
            break

        if choice == _QUIT:
            unreviewed = len(proposed) - index + 1
            output_fn(
                f"  Review stopped — the remaining {unreviewed} "
                "item(s) were not stored."
            )
            break
        if choice == _DECLINE:
            rejected += 1
            continue

        knowledge_id = _store_entry(store, entry, on_stored)
        # store() dedups within (category, context, entity), so two approved
        # entries can merge into one row — count rows, not approvals.
        if knowledge_id not in knowledge_ids:
            knowledge_ids.append(knowledge_id)

    return BootstrapResult(
        stored=len(knowledge_ids),
        skipped=skipped,
        rejected=rejected,
        unreviewed=unreviewed,
        cancelled=cancelled,
        answers=answers,
        knowledge_ids=knowledge_ids,
    )
