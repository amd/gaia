# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Find what is worth learning in the agent's history.

Four kinds of learning opportunity:

- **preference** — something the user told the agent about how they like
  things done (a ``preference`` memory).
- **concept** — something specific to the user's world the base model can't
  know: their projects, jargon, people, systems (``fact``/``note``/``skill``
  memories).
- **workflow** — a turn where the agent got there the long way: failed or
  repeated tool calls before the one that worked. The training example is the
  direct path.
- **correction** — the user said the answer was wrong and the agent then got it
  right. The training example pairs the original question with the right
  answer.

Preferences and concepts are expanded into several question/answer pairs by a
*teacher* (the local base model, prompted for JSON), so the adapter learns to
apply the fact, not to recite one sentence. Every opportunity also yields
held-out *probes* the eval gate asks before and after training.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from gaia.learning.sources import History, ToolCall, Turn, parse_ts

Teacher = Callable[[str], str]

KINDS = ("preference", "concept", "workflow", "correction")

_CORRECTION = re.compile(
    r"^\s*(no\b|nope\b|wrong\b|that'?s (not|wrong|incorrect)|not quite|"
    r"incorrect\b|actually\b|i meant\b|you got .* wrong|that is (not|wrong))",
    re.IGNORECASE,
)

# Tools whose output is untrusted outside content. A turn that read such
# content never becomes a training target, so injected text can't be learned.
_UNTRUSTED_TOOL_HINTS = ("web", "fetch", "browse", "url", "email", "mail", "rss")

_STOPWORDS = frozenset(
    "the a an and or of to in on at for with is are was were be been it this "
    "that these those you your i we they he she them his her its our their my "
    "me as by from but not no so if then than there here what which who how "
    "when where why can could should would will just also only into about".split()
)


@dataclass
class Probe:
    """A held-out question the eval gate asks the model."""

    question: str
    kind: str = "mentions"  # "mentions" or "first_tool"
    must_mention: List[str] = field(default_factory=list)
    expect_tool: Optional[str] = None
    tools: List[Dict[str, Any]] = field(default_factory=list)
    opportunity: str = ""


@dataclass
class Opportunity:
    id: str
    kind: str
    title: str
    evidence: List[str]
    examples: List[List[Dict[str, Any]]] = field(default_factory=list)
    probes: List[Probe] = field(default_factory=list)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Report:
    opportunities: List[Opportunity]
    turns_read: int
    facts_read: int
    tool_calls_read: int
    skipped: Dict[str, int] = field(default_factory=dict)

    def counts(self) -> Dict[str, int]:
        out = {k: 0 for k in KINDS}
        for o in self.opportunities:
            if not o.error:
                out[o.kind] += 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opportunities": [o.to_dict() for o in self.opportunities],
            "turns_read": self.turns_read,
            "facts_read": self.facts_read,
            "tool_calls_read": self.tool_calls_read,
            "skipped": self.skipped,
            "counts": self.counts(),
        }


def _keywords(text: str, limit: int = 3, exclude: str = "") -> List[str]:
    """The words of *text* most likely to carry its fact, best first: numbers
    and identifiers, then mid-sentence capitalised names, then long words.
    Words in *exclude* (the wrong answer, the question) and sentence-initial
    words ("Right", "Thanks") never count."""
    excluded = {w.lower().strip(".:,;-/") for w in re.findall(r"[\w#@./:-]+", exclude)}
    ranked: List[Tuple[int, int, str]] = []
    seen = set()
    sentence_start = True
    for n, raw in enumerate(re.findall(r"[\w#@./:-]+|[.!?]", text)):
        if raw in ".!?":
            sentence_start = True
            continue
        w = raw.strip(".:,;-/")
        lw = w.lower()
        first, sentence_start = sentence_start, raw.endswith((".", "!", "?"))
        if len(w) < 2 or lw in _STOPWORDS or lw in excluded or lw in seen:
            continue
        if (
            any(c.isdigit() for c in w)
            or any(c in w for c in "#@/_")
            or ("-" in w and w[0].isalpha())
        ):
            rank = 0
        elif w[0].isupper() and not first:
            rank = 1
        elif len(w) >= 7:
            rank = 2
        else:
            continue
        seen.add(lw)
        ranked.append((rank, n, w))
    ranked.sort()
    # Long ordinary words are a last resort: "correction" in "thanks for the
    # correction" is not the fact a right answer must repeat.
    strong = [w for rank, _, w in ranked if rank < 2]
    return (strong or [w for _, _, w in ranked])[:limit]


def _untrusted(turn: Turn) -> bool:
    return any(h in s.tool.lower() for s in turn.steps for h in _UNTRUSTED_TOOL_HINTS)


def _forgotten_in(text: str, forgotten: List[str]) -> bool:
    low = text.lower()
    return any(f and f.lower() in low for f in forgotten)


_SYNTH_PROMPT = """You are preparing training data so a personal assistant learns one thing about its user.

What it must learn ({kind}):
{fact}

Write {n} different, realistic requests the user might send where this matters, each with the ideal reply that correctly applies it. Vary the wording and the situation. Replies must be correct, concise and consistent with the fact; never invent details that the fact does not support.

Then write 3 different test questions (not among the {n} above) whose correct answer depends on the fact, and for each list 1-2 key terms that any correct answer must contain: a name, number, code or single distinctive word, copied exactly from the fact.

Reply with JSON only:
{{"examples": [{{"question": "...", "answer": "..."}}], "probes": [{{"question": "...", "must_mention": ["..."]}}]}}"""


_CHECK_PROMPT = """Fact: {fact}

Using the fact, answer in one or two sentences: {question}"""

# Longest required phrase a probe may name; longer ones fail correct paraphrases.
_MAX_TERM_WORDS = 2


def _parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the teacher's reply")
    return json.loads(text[start : end + 1])


class Analyzer:
    def __init__(self, teacher: Teacher, *, examples_per_fact: int = 10):
        self.teacher = teacher
        self.examples_per_fact = examples_per_fact

    def analyze(
        self,
        history: History,
        *,
        max_examples: int,
        known: Optional[Dict[str, str]] = None,
    ) -> Report:
        """*known* maps learned item ids to when they were last updated; a
        memory already learned and unchanged since is not re-synthesised."""
        known = known or {}
        skipped: Dict[str, int] = {}
        opps: List[Opportunity] = []
        for fact in history.facts:
            if _forgotten_in(fact.content, history.forgotten):
                skipped["forgotten"] = skipped.get("forgotten", 0) + 1
                continue
            kind = "preference" if fact.category == "preference" else "concept"
            learned_at = parse_ts(known.get(f"{kind}:{fact.id}"))
            changed_at = parse_ts(fact.updated_at)
            if learned_at and changed_at and changed_at <= learned_at:
                skipped["already_learned"] = skipped.get("already_learned", 0) + 1
                continue
            opps.append(self._from_fact(fact.id, kind, fact.content))
        opps.extend(self._corrections(history, skipped))
        opps.extend(self._workflows(history, skipped))
        self._cap(opps, max_examples)
        return Report(
            opportunities=opps,
            turns_read=len(history.turns),
            facts_read=len(history.facts),
            tool_calls_read=len(history.tool_calls),
            skipped=skipped,
        )

    # -- preferences and concepts ------------------------------------------

    def _from_fact(self, fact_id: str, kind: str, content: str) -> Opportunity:
        opp = Opportunity(
            id=f"{kind}:{fact_id}",
            kind=kind,
            title=content if len(content) <= 120 else content[:117] + "...",
            evidence=[content],
        )
        self._synthesize(opp, content)
        if not opp.examples and not opp.error:
            opp.error = "The teacher model wrote no training examples."
        return opp

    def _synthesize(self, opp: Opportunity, fact: str) -> None:
        """Add teacher-written examples and probes for *fact* to *opp*."""
        prompt = _SYNTH_PROMPT.format(
            kind=opp.kind, fact=fact, n=self.examples_per_fact
        )
        try:
            data = _parse_json(self.teacher(prompt))
        except (ValueError, json.JSONDecodeError) as e:
            opp.error = f"The teacher model returned no usable JSON: {e}"
            return
        for ex in data.get("examples") or []:
            q, a = (
                str(ex.get("question", "")).strip(),
                str(ex.get("answer", "")).strip(),
            )
            if q and a:
                opp.examples.append(
                    [
                        {"role": "user", "content": q},
                        {"role": "assistant", "content": a},
                    ]
                )
        low = fact.lower()
        for p in data.get("probes") or []:
            q = str(p.get("question", "")).strip()
            # Short terms really in the fact: an invented or long phrase would
            # fail a correct answer.
            must = [
                t
                for t in (str(m).strip() for m in p.get("must_mention") or [])
                if t and t.lower() in low and len(t.split()) <= _MAX_TERM_WORDS
            ][:2]
            if q and must and self._answerable(fact, q, must):
                opp.probes.append(
                    Probe(question=q, must_mention=must, opportunity=opp.id)
                )

    def _answerable(self, fact: str, question: str, must: List[str]) -> bool:
        """A probe counts only if the teacher, shown the fact, passes it — so a
        failure measures what the adapter didn't learn, not a bad question."""
        reply = self.teacher(_CHECK_PROMPT.format(fact=fact, question=question)).lower()
        return all(m.lower() in reply for m in must)

    # -- corrections -------------------------------------------------------

    def _corrections(
        self, history: History, skipped: Dict[str, int]
    ) -> List[Opportunity]:
        by_session: Dict[str, List[Turn]] = {}
        for t in history.turns:
            by_session.setdefault(t.session_id, []).append(t)
        out = []
        for turns in by_session.values():
            i = 0
            while i < len(turns) - 1:
                if not _CORRECTION.search(turns[i + 1].question):
                    i += 1
                    continue
                j = i + 1
                while j + 1 < len(turns) and _CORRECTION.search(turns[j + 1].question):
                    j += 1
                original, complaint, fixed = turns[i], turns[i + 1], turns[j]
                i = j + 1
                text = original.question + fixed.answer
                if _untrusted(original) or _untrusted(fixed):
                    skipped["untrusted_content"] = (
                        skipped.get("untrusted_content", 0) + 1
                    )
                    continue
                if _forgotten_in(text, history.forgotten):
                    skipped["forgotten"] = skipped.get("forgotten", 0) + 1
                    continue
                must = _keywords(
                    fixed.answer, exclude=original.answer + " " + original.question
                )
                opp = Opportunity(
                    id=f"correction:{original.question_id}",
                    kind="correction",
                    title=f"Corrected answer to: {original.question[:90]}",
                    evidence=[
                        f"First answer: {original.answer[:300]}",
                        f"User: {complaint.question[:300]}",
                        f"Accepted answer: {fixed.answer[:300]}",
                    ],
                    examples=[
                        [
                            {"role": "user", "content": original.question},
                            {"role": "assistant", "content": fixed.answer},
                        ]
                    ],
                )
                if must:
                    opp.probes.append(
                        Probe(
                            question=original.question,
                            must_mention=must,
                            opportunity=opp.id,
                        )
                    )
                # One exchange is too little to change the model's answer;
                # expand the corrected fact like any other.
                self._synthesize(
                    opp,
                    f'Asked "{original.question}", the right answer is: {fixed.answer}',
                )
                if opp.error:
                    # Still learnable from the exchange itself; say why it's thin.
                    opp.evidence.append(f"Not expanded: {opp.error}")
                    opp.error = None
                out.append(opp)
        return out

    # -- workflows ---------------------------------------------------------

    def _workflows(
        self, history: History, skipped: Dict[str, int]
    ) -> List[Opportunity]:
        out = []
        for turn in history.turns:
            if not turn.steps:
                continue
            calls = self._calls_for(turn, history.tool_calls)
            if not calls:
                continue
            useful, wasted = _direct_path(calls)
            if not wasted or not useful:
                continue
            if _untrusted(turn):
                skipped["untrusted_content"] = skipped.get("untrusted_content", 0) + 1
                continue
            if _forgotten_in(turn.question + turn.answer, history.forgotten):
                skipped["forgotten"] = skipped.get("forgotten", 0) + 1
                continue
            tools = _tool_schemas(calls)
            messages: List[Dict[str, Any]] = [
                {"role": "user", "content": turn.question}
            ]
            for n, c in enumerate(useful):
                call_id = f"call_{n}"
                messages.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": c.tool_name, "arguments": c.args},
                            }
                        ],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": c.tool_name,
                        "content": c.result_summary or "done",
                    }
                )
            messages.append({"role": "assistant", "content": turn.answer})
            opp_id = f"workflow:{turn.question_id}"
            out.append(
                Opportunity(
                    id=opp_id,
                    kind="workflow",
                    title=(
                        f"Go straight to {useful[0].tool_name} "
                        f"({len(calls)} calls → {len(useful)})"
                    ),
                    evidence=[
                        f"Request: {turn.question[:200]}",
                        *[
                            f"Wasted: {c.tool_name}({json.dumps(c.args)[:120]}) "
                            + (
                                f"failed: {c.error[:120]}"
                                if not c.success
                                else "repeated"
                            )
                            for c in wasted
                        ],
                    ],
                    examples=[messages],
                    tools=tools,
                    probes=[
                        Probe(
                            question=turn.question,
                            kind="first_tool",
                            expect_tool=useful[0].tool_name,
                            tools=tools,
                            opportunity=opp_id,
                        )
                    ],
                )
            )
        return out

    @staticmethod
    def _calls_for(turn: Turn, calls: List[ToolCall]) -> List[ToolCall]:
        """The recorded calls behind *turn*'s steps: made between the question
        and the answer, matched in order by tool name. The memory store keys
        calls by its own session id, so time is the join."""
        start, end = parse_ts(turn.asked_at), parse_ts(turn.answered_at)
        if start is None or end is None:
            return []
        slack = timedelta(seconds=5)
        window = [
            c
            for c in calls
            if (ts := parse_ts(c.timestamp)) is not None
            and start - slack <= ts <= end + slack
        ]
        wanted = [s.tool for s in turn.steps]
        matched, k = [], 0
        for call in window:
            if k < len(wanted) and call.tool_name == wanted[k]:
                matched.append(call)
                k += 1
        return matched if k == len(wanted) else []

    @staticmethod
    def _cap(opps: List[Opportunity], max_examples: int) -> None:
        total = 0
        for o in opps:
            room = max(0, max_examples - total)
            o.examples = o.examples[:room]
            total += len(o.examples)


def _direct_path(calls: List[ToolCall]) -> Tuple[List[ToolCall], List[ToolCall]]:
    """Split calls into the direct path and the waste: failures and exact
    repeats of an earlier successful call."""
    useful, wasted, seen = [], [], set()
    for c in calls:
        key = (c.tool_name, json.dumps(c.args, sort_keys=True))
        if not c.success or key in seen:
            wasted.append(c)
            continue
        seen.add(key)
        useful.append(c)
    return useful, wasted


def _tool_schemas(calls: List[ToolCall]) -> List[Dict[str, Any]]:
    """Minimal OpenAI tool schemas for the tools a workflow used, so the
    training example renders with the tools declared as at inference."""
    props: Dict[str, Dict[str, Any]] = {}
    for c in calls:
        entry = props.setdefault(c.tool_name, {})
        for k, v in c.args.items():
            entry[k] = {"type": "number" if isinstance(v, (int, float)) else "string"}
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"The {name} tool.",
                "parameters": {"type": "object", "properties": p},
            },
        }
        for name, p in props.items()
    ]
