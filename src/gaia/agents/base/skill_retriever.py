# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SkillRetriever — match a user's turn against skills that are installed but *not* loaded.

The gap this closes
-------------------
:mod:`gaia.agents.base.skill_loader` decides which **already-loaded** skill's body
renders this turn. Nothing decided which skill to load in the first place, so the
agent only ever knew a skill existed once the user named it. Asked *"what's been
going on in my github inbox?"* with ``github-triage`` sitting installed, the
flagship called zero tools and answered from its memory store — a confident,
entirely fabricated answer. Only ``"Load the github-triage skill."`` worked.

Users do not know skill names. This module makes the *description* the index.

Why lexical, not embeddings
---------------------------
Every installed skill already carries a ``description`` written to be matched on
("Use when the user asks to triage issues, review a backlog…"). Against that
corpus a normalized BM25 is the right tool, and the reasons are measurable rather
than aesthetic:

* **Corpus size.** ~10-25 skills. An ANN index is overhead with no recall to buy.
* **Latency.** This runs on *every* turn, before the first token. Scoring is
  pure-Python over a prebuilt index and measures in tens of microseconds; an
  embedding call is a network round-trip to a single-slot backend.
  :mod:`gaia.agents.base.skill_loader` can afford one because it short-circuits
  when nothing is loaded — the discovery path cannot, it runs precisely when
  *nothing* is loaded.
* **Failure mode.** An embedder outage disables selection (see
  ``SkillLoader.session_disabled``). A lexical index has no runtime dependency
  to lose.

``tests/unit/test_skill_retriever.py`` carries the labeled benchmark this is
calibrated against; every constant below moves its numbers.

Confidence, not guessing
------------------------
:meth:`SkillRetriever.rank` returns scored candidates;
:meth:`SkillRetriever.decide` turns them into exactly one of three outcomes:

``AUTO``       one skill clears the score floor, carries enough rare-term
               evidence, and beats the runner-up by a margin — load it.
``SHORTLIST``  several plausible, none dominant — name them to the model and
               let it call ``load_skill``, rather than picking for it.
``NONE``       no real lexical evidence — proceed with no skill.

The margin rule is what keeps "never guess" honest: a tie is reported as a tie.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, FrozenSet, Iterable, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.skills.format import Skill

# ── tuning constants ────────────────────────────────────────────────────────
# Calibrated against the labeled query set in tests/unit/test_skill_retriever.py.
# Changing one without re-running that benchmark is how a silent recall
# regression ships.

#: BM25 term-frequency saturation. Standard Okapi defaults.
K1 = 1.2
B = 0.75

#: How many times a skill's own *name* tokens are folded into its document.
#: "triage my github inbox" should beat a skill that merely mentions GitHub in
#: passing; the name is the strongest single signal a skill carries.
NAME_WEIGHT = 3

#: Fraction of the query's total discriminative mass the winner must capture.
#: At 0.50 a skill has to account for half of what the query is *about* before
#: it loads on its own.
MIN_SCORE = 0.50

#: IDF mass the winner's matched terms must carry, as a fraction of the rarest
#: term in the corpus — so a match built entirely from terms that appear in
#: nearly every skill cannot win.
#:
#: A *fraction*, not an absolute, because IDF scales with corpus size: a
#: one-skill-only term is worth 1.39 in a 5-skill library and 2.93 in a 30-skill
#: one. An absolute floor of 1.5 silently disabled auto-load entirely for small
#: libraries — "anything new on github?" scored 0.75 and still refused to load,
#: because the corpus was too small for any term to be worth 1.5.
MIN_IDF_FRACTION = 0.6

#: How far ahead of the runner-up the winner must be to auto-load. Below this
#: the result is a SHORTLIST — reported, never resolved by coin-flip.
#:
#: 1.3 is where the joint sweep's auto-recall peaks (0.742 vs 0.710) at the same
#: zero false loads, but the whole gap is one query that shortlists instead of
#: loading, and shortlist recall is identical either way. The wider margin buys
#: insurance against queries the benchmark does not contain for a cost of one
#: extra tool call on one query — take the insurance.
MARGIN = 1.5

#: Cap on how many near-misses are named to the model. Each costs prompt tokens,
#: and a list long enough to need scanning is not a shortlist.
MAX_SHORTLIST = 3

#: Distinct query terms a candidate must match to count as evidence when none of
#: them is *strong* (see :data:`_WEAK_ALONE`).
MIN_MATCHED_TERMS = 2

#: Verbs that describe what an assistant does rather than what a skill is about.
#:
#: Every false auto-load the benchmark produced had the same shape: one generic
#: verb from a description and nothing else — ``remember`` pulling in
#: ``source-watch`` for "remember my favourite colour is teal", ``explain``
#: pulling in ``coding`` for a question about mutexes, ``write`` pulling in
#: ``research-report`` for a haiku. Normalizing by the query's *known* terms makes
#: a lone match look like total agreement, because the words the corpus never
#: heard of are exactly the ones proving the query is about something else.
#:
#: IDF cannot separate these: in an 11-skill corpus ``remember`` and ``contract``
#: both appear in exactly one description, so both score 2.08. What separates
#: them is that one names the *work* and the other names the *subject*. This set
#: is therefore small and deliberately verb-only — a noun goes in it only over a
#: benchmark regression.
#:
#: A term here is still full evidence when it is part of the skill's own name
#: (``check`` for ``check-in``), because a name is identity, not description.
_WEAK_ALONE = frozenset("""
add answer apply build call change check choose compose create draft edit explain
find fix follow generate handle keep know list look manage open pick prepare produce
provide put read record remember review run save say search see send set show start
stop support tell think try turn update work write
""".split())

#: Score a candidate needs to reach to be worth naming at all.
SHORTLIST_FLOOR = 0.18

#: How much a query word the corpus has never seen counts *against* a match,
#: as a fraction of the rarest-term IDF.
#:
#: Without this the denominator is built only from terms the corpus knows, so
#: "what is the contract for this API endpoint?" scored 0.70 for ``xlsx`` — one
#: incidental ``api`` out of three content words looked like near-total
#: agreement, because the two words that prove the query is about something else
#: were simply discarded. An unseen word is not neutral evidence: in this corpus
#: it is maximally rare, and it discriminates against every skill at once.
UNKNOWN_WEIGHT = 0.6

_WORD = re.compile(r"[a-z0-9]+")

#: Closed-class words plus the vocabulary of *asking*. "use", "when", "user" and
#: "skill" appear in nearly every SKILL.md description by convention, so they
#: carry no discriminative signal and only inflate the denominator.
_STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been
before being below between both but by can cannot could did do does doing don down
during each few for from further had has have having he her here hers herself him
himself his how i if in into is it its itself just me more most my myself no nor not
now of off on once only or other ought our ours ourselves out over own same she should
so some such than that the their theirs them themselves then there these they this
those through to too under until up very was we were what when where which while who
whom why with would you your yours yourself yourselves
use uses used using user users skill skills agent asks ask asked need needs want wants
please help make makes made get gets got give gives take takes let lets thing things
mean means meaning kind sort lot bit way ways one two
yes no ok okay sure yeah yep nope thanks thank hi hello hey cool great nice go ahead
please anything something nothing everything
""".split())

#: Deliberately tiny and suffix-only. A real stemmer would need a dependency and
#: conflates words this corpus needs kept apart. Plurals normally lose only the
#: final ``s`` (``notes`` -> ``note``); the spellings below need ``es`` removed
#: to keep their singular form (``boxes`` -> ``box``).
_SUFFIXES = ("ing", "ed", "s")
_ES_PLURAL_SUFFIXES = ("ches", "shes", "sses", "xes", "zes")

#: Shortest a stem may be. Below this, suffix stripping starts merging unrelated
#: words ("gas" -> "ga") and a three-letter stem matches far too much.
_MIN_STEM = 3


def _stem(word: str) -> str:
    """Strip a common English suffix, never taking a word below :data:`_MIN_STEM`."""
    if word.endswith("ies") and len(word) > 5:
        return word[:-3] + "y"
    if word.endswith(_ES_PLURAL_SUFFIXES) and len(word) - 2 >= _MIN_STEM:
        return word[:-2]
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens, stopwords dropped, lightly stemmed."""
    return [
        _stem(word)
        for word in _WORD.findall((text or "").lower())
        if word not in _STOPWORDS and len(word) > 1
    ]


def _name_tokens(name: str) -> List[str]:
    """A skill name's own words — ``github-triage`` -> ``["github", "triage"]``."""
    return [_stem(part) for part in _WORD.findall((name or "").lower()) if part]


@dataclass(frozen=True)
class SkillCandidate:
    """One scored skill from :meth:`SkillRetriever.rank`."""

    name: str
    #: Fraction of the query's corpus-relevant IDF mass this skill matched, in [0, 1].
    score: float
    #: Absolute IDF mass of the matched terms — the "is this real evidence" check.
    idf_mass: float
    #: The query terms that hit this skill, rarest first.
    matched: Tuple[str, ...] = ()
    #: Whether any matched term is a token of the skill's own name.
    name_hit: bool = False
    #: Matched terms that are not generic assistant verbs, or that name the skill.
    strong: Tuple[str, ...] = ()

    @property
    def has_evidence(self) -> bool:
        """Whether this match is about the skill's subject, not just its verbs.

        One term naming the subject ("contract", "csv", "github") is evidence;
        one generic verb ("write", "explain") is not, however rare it happens to
        be in a small corpus. See :data:`_WEAK_ALONE`.
        """
        return bool(self.strong) or len(self.matched) >= MIN_MATCHED_TERMS

    def __str__(self) -> str:  # pragma: no cover - logging convenience
        return f"{self.name}={self.score:.2f}"


@dataclass(frozen=True)
class Decision:
    """What the retriever concluded for one turn.

    At most one of *load* / *shortlist* is populated; both empty means "no skill
    is relevant, proceed as normal".
    """

    #: The single skill confident enough to load without asking, or ``None``.
    load: Optional[str] = None
    #: Plausible-but-not-dominant names to offer the model, best first.
    shortlist: Tuple[str, ...] = ()
    #: Every scored candidate, best first — for logging and tests.
    ranked: Tuple[SkillCandidate, ...] = field(default=())

    @property
    def outcome(self) -> str:
        """``"auto"`` / ``"shortlist"`` / ``"none"`` — for logs and assertions."""
        if self.load:
            return "auto"
        return "shortlist" if self.shortlist else "none"


class SkillRetriever:
    """Normalized-BM25 index over installed skills' ``name`` + ``description``.

    Build it from whatever :class:`~gaia.skills.manager.SkillManager` discovered
    (frontmatter only — bodies are never read here, so indexing a whole library
    costs one directory scan). Rebuild with :meth:`index` when the installed set
    changes; scoring allocates nothing per query beyond the query's token list.
    """

    def __init__(self, skills: Optional[Dict[str, "Skill"]] = None) -> None:
        self._docs: Dict[str, List[str]] = {}
        self._tf: Dict[str, Dict[str, int]] = {}
        self._idf: Dict[str, float] = {}
        self._doc_len: Dict[str, float] = {}
        self._name_terms: Dict[str, FrozenSet[str]] = {}
        self._avg_len: float = 1.0
        self._max_idf: float = 0.0
        self._fingerprint: Tuple = ()
        if skills:
            self.index(skills)

    # ── indexing ────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        """Number of indexed skills."""
        return len(self._docs)

    @property
    def names(self) -> List[str]:
        """Indexed skill names, sorted."""
        return sorted(self._docs)

    @staticmethod
    def fingerprint(skills: Dict[str, "Skill"]) -> Tuple:
        """Cheap identity of an installed set — reindex only when this changes."""
        return tuple(
            sorted((name, (skill.description or "")) for name, skill in skills.items())
        )

    def index(self, skills: Dict[str, "Skill"]) -> None:
        """(Re)build the index from ``{name: Skill}`` metadata."""
        self.index_texts(
            {name: (skill.description or "") for name, skill in skills.items()}
        )
        self._fingerprint = self.fingerprint(skills)

    def index_texts(self, descriptions: Dict[str, str]) -> None:
        """(Re)build the index from ``{name: description}`` — the testable core."""
        self._docs = {}
        self._tf = {}
        self._doc_len = {}
        self._name_terms = {}
        for name, description in descriptions.items():
            own = _name_tokens(name)
            self._name_terms[name] = frozenset(own)
            tokens = own * NAME_WEIGHT + tokenize(description)
            self._docs[name] = tokens
            counts: Dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            self._tf[name] = counts
            self._doc_len[name] = float(len(tokens))

        total = len(self._docs)
        self._avg_len = (sum(self._doc_len.values()) / total if total else 0.0) or 1.0

        df: Dict[str, int] = {}
        for counts in self._tf.values():
            for token in counts:
                df[token] = df.get(token, 0) + 1
        # Always-positive IDF (the Lucene / BM25+ variant). The textbook form
        # goes negative for a term in more than half the corpus, which with
        # N=20 would let a common word *subtract* from a genuine match.
        self._idf = {
            token: math.log(1.0 + (total - count + 0.5) / (count + 0.5))
            for token, count in df.items()
        }
        # The IDF an unseen term is charged at — a term in exactly one skill.
        self._max_idf = max(self._idf.values(), default=0.0)

    def is_stale(self, skills: Dict[str, "Skill"]) -> bool:
        """True when *skills* differs from what is currently indexed."""
        return self.fingerprint(skills) != self._fingerprint

    # ── scoring ─────────────────────────────────────────────────────────────

    def rank(self, query: str, *, exclude: Iterable[str] = ()) -> List[SkillCandidate]:
        """Score every indexed skill against *query*, best first.

        Args:
            query: The user's turn (optionally with prior context prepended).
            exclude: Names to leave out — normally the already-loaded set.

        Returns:
            Candidates with a non-zero score, sorted by score then name so equal
            scores order deterministically.
        """
        if not self._docs:
            return []
        skip = set(exclude)

        # The denominator is the query's whole discriminative mass, not just
        # the part this corpus happens to know. Terms it has never seen are
        # charged at UNKNOWN_WEIGHT x the rarest known IDF — see that constant
        # for why discarding them turns one incidental hit into a 0.70.
        all_terms = list(dict.fromkeys(tokenize(query)))
        terms = [t for t in all_terms if t in self._idf]
        if not terms:
            return []
        unknown = len(all_terms) - len(terms)
        query_mass = sum(self._idf[t] for t in terms) + (
            unknown * UNKNOWN_WEIGHT * self._max_idf
        )
        if query_mass <= 0:
            return []

        results: List[SkillCandidate] = []
        for name, counts in self._tf.items():
            if name in skip:
                continue
            norm = K1 * (1.0 - B + B * self._doc_len[name] / self._avg_len)
            raw = 0.0
            mass = 0.0
            hits: List[Tuple[float, str]] = []
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                idf = self._idf[term]
                raw += idf * (tf * (K1 + 1.0)) / (tf + norm)
                mass += idf
                hits.append((idf, term))
            if raw <= 0:
                continue
            hits.sort(reverse=True)
            matched = tuple(term for _, term in hits)
            own = self._name_terms[name]
            results.append(
                SkillCandidate(
                    name=name,
                    score=min(raw / query_mass, 1.0),
                    idf_mass=mass,
                    matched=matched,
                    name_hit=bool(own.intersection(matched)),
                    strong=tuple(
                        t for t in matched if t in own or t not in _WEAK_ALONE
                    ),
                )
            )
        results.sort(key=lambda c: (-c.score, c.name))
        return results

    def decide(
        self,
        query: str,
        *,
        exclude: Iterable[str] = (),
        min_score: Optional[float] = None,
    ) -> Decision:
        """Rank, then apply the confidence bar. See the module docstring.

        ``min_score`` overrides :data:`MIN_SCORE` for this call only — the bar is
        per-caller, so it must not be smuggled through module state that a
        concurrent agent in the same process would observe.
        """
        floor = MIN_SCORE if min_score is None else min_score
        ranked = self.rank(query, exclude=exclude)
        if not ranked:
            return Decision(ranked=())

        # Drop matches built only from generic assistant verbs before anything
        # ranks — they are noise for auto-load and for the shortlist alike, and
        # leaving them in would put "source-watch" in front of the model on a
        # turn about the user's favourite colour.
        credible = [c for c in ranked if c.has_evidence]
        if not credible:
            return Decision(ranked=tuple(ranked))

        top = credible[0]
        runner_up = credible[1].score if len(credible) > 1 else 0.0
        confident = (
            top.score >= floor
            and top.idf_mass >= MIN_IDF_FRACTION * self._max_idf
            and top.score >= runner_up * MARGIN
        )
        if confident:
            return Decision(load=top.name, ranked=tuple(ranked))

        shortlist = tuple(
            c.name for c in credible[:MAX_SHORTLIST] if c.score >= SHORTLIST_FLOOR
        )
        return Decision(shortlist=shortlist, ranked=tuple(ranked))


__all__ = [
    "SkillRetriever",
    "SkillCandidate",
    "Decision",
    "tokenize",
    "MIN_SCORE",
    "MIN_IDF_FRACTION",
    "MARGIN",
    "MAX_SHORTLIST",
    "MIN_MATCHED_TERMS",
    "SHORTLIST_FLOOR",
    "UNKNOWN_WEIGHT",
    "NAME_WEIGHT",
]
