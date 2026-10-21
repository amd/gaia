# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scale loaded-skill bodies and skill-provided tools to the model's context.

A skill body that helps a 200K-context cloud model can crowd a 64K local model
out of its own instructions: on one such model, loading two or three skills and
their tools raised every request from about 23K to 40K tokens, and instruction
following dropped. So each turn the bodies of loaded skills share a budget
sized from the active model's context window:

* **Budget** — :data:`SKILL_BUDGET_FRACTION` of the window, clamped to
  :data:`SKILL_BUDGET_FLOOR` .. :data:`SKILL_BUDGET_CEILING` tokens. The window
  is the one the history budget uses (:func:`gaia.agents.base.history.context_window`),
  except that a Lemonade cloud model, admitted at 64K by policy rather than by
  its own window, uses the window Lemonade's catalogue lists for it, or the
  ceiling when it lists none.
* **Bodies** — when the bodies selected for this turn exceed the budget, they
  are ranked: skills explicitly loaded (by the model, the user, or auto-load)
  since the last turn began, most recent first, then by this turn's relevance
  score, then most recently loaded. The first is always shown in full; each
  later one is shown in full if it still fits, otherwise as a one-line summary
  that says how to get the rest back.
* **Auto-load** — on a window of :data:`SMALL_WINDOW_TOKENS` or less, at most
  :data:`SMALL_WINDOW_AUTOLOADS` skill is auto-loaded per turn.
* **Tools** — on the same small windows, the tools a loaded skill *provides*
  (its ``tools.py``, registered as ``<skill>/<tool>``) are left out of the
  turn's tool list unless its body is shown in full. They stay registered, so
  a call still runs, and ``load_skill`` brings them back with the body. Only
  the dynamic tool filter is narrowed: with ``GAIA_DYNAMIC_TOOLS`` off every
  registered tool is sent, as before.

Opt-in per agent with :attr:`Agent.SKILL_CONTEXT_BUDGET`; every other agent's
prompt is unchanged. The plan for the current turn is kept on the agent and
reported by :func:`skill_budget_report` for the turn's diagnostics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from gaia.logger import get_logger

log = get_logger(__name__)

#: Share of the context window the loaded skill bodies may take.
SKILL_BUDGET_FRACTION = 0.05

#: Smallest budget, so a 16K model can still hold one short skill alongside a
#: summary of the rest.
SKILL_BUDGET_FLOOR = 1_000

#: Largest budget: the ~6,000 tokens #5442 capped auto-load at, which two of the
#: largest hub skills (~2.1K-2.8K each) fit inside. Reached at a 120K window.
SKILL_BUDGET_CEILING = 6_000

#: A window this size or smaller auto-loads one skill per turn and only sends
#: the tools of skills whose bodies are shown in full.
SMALL_WINDOW_TOKENS = 65_536

#: Most skills auto-loaded per turn on a small window.
SMALL_WINDOW_AUTOLOADS = 1

#: Reference files named on a summarized skill's line.
SUMMARY_REFERENCES_SHOWN = 5


def skill_body_budget(window: Optional[int]) -> int:
    """Tokens the loaded skill bodies may take for a *window*-token model.

    ``None`` (a cloud model whose window is not known) gets the ceiling.
    """
    if window is None:
        return SKILL_BUDGET_CEILING
    scaled = int(window * SKILL_BUDGET_FRACTION)
    return max(SKILL_BUDGET_FLOOR, min(SKILL_BUDGET_CEILING, scaled))


def is_small_window(window: Optional[int]) -> bool:
    """True for a known window of :data:`SMALL_WINDOW_TOKENS` or less."""
    return window is not None and window <= SMALL_WINDOW_TOKENS


def body_tokens(skill: Any) -> int:
    """Prompt tokens *skill*'s body costs, on the 4-chars-per-token estimate."""
    from gaia.agents.tools.skill_library_tools import estimate_prompt_tokens

    return estimate_prompt_tokens(getattr(skill, "body", "") or "")


def resolve_skill_window(agent: Any) -> Tuple[Optional[int], str]:
    """The window the skill budget is sized from, and where it came from."""
    from gaia.agents.base.history import _is_cloud, context_window

    if not getattr(agent, "_use_claude", False) and _is_cloud(agent):
        from gaia.llm.lemonade_client import cloud_model_info

        info = cloud_model_info(getattr(agent, "model_id", None)) or {}
        listed = info.get("ctx_size")
        if isinstance(listed, int) and not isinstance(listed, bool) and listed > 0:
            return listed, "cloud catalogue"
        return None, "cloud model with no window in Lemonade's catalogue"
    return context_window(agent), "model context window"


@dataclass
class SkillBudget:
    """One turn's skill-body budget and what it decided."""

    window: Optional[int]
    window_source: str
    budget: int
    small_window: bool
    #: Loaded skills whose body is shown in full, in rank order.
    full: List[str] = field(default_factory=list)
    #: Relevant loaded skills cut to a one-line summary to fit the budget.
    summarized: List[str] = field(default_factory=list)
    #: Tokens of the bodies shown in full, always-on skills included.
    used_tokens: int = 0
    #: Tokens the selected bodies would have taken with nothing summarized.
    wanted_tokens: int = 0
    #: Tools provided by loaded skills not shown in full, kept out of the
    #: turn's tool list (small windows with a dynamic tool filter only).
    withheld_tools: List[str] = field(default_factory=list)
    #: Most skills auto-loaded per turn, or ``None`` for no window-based limit.
    autoload_limit: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        """JSON-ready form for the turn record and the context panel."""
        return asdict(self)


def window_budget(agent: Any) -> Tuple[Optional[int], str, int]:
    """``(window, source, budget)`` for *agent*'s model, cached per model.

    Cached because the window it resolves to must not drift from turn to turn
    with free memory: a budget that moved would re-render the skills section,
    and with it the whole prompt, for no change the user made.
    """
    key = (getattr(agent, "model_id", None), bool(getattr(agent, "_use_claude", 0)))
    cached = getattr(agent, "_skill_window_cache", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    window, source = resolve_skill_window(agent)
    value = (window, source, skill_body_budget(window))
    agent._skill_window_cache = (key, value)
    log.debug(
        "[skills] body budget %d tokens for %s (window %s from %s)",
        value[2],
        key[0],
        window,
        source,
    )
    return value


def enabled(agent: Any) -> bool:
    """True when *agent* opted into the skill context budget."""
    return bool(getattr(agent, "SKILL_CONTEXT_BUDGET", False))


def note_skill_request(agent: Any, name: str) -> None:
    """Record an explicit load of *name*: it ranks first at the next fit."""
    if not enabled(agent):
        return
    requested = getattr(agent, "_skill_requests", None)
    if requested is None:
        requested = agent._skill_requests = []
    if name in requested:
        requested.remove(name)
    requested.insert(0, name)


def rank_skills(
    candidates: Sequence[str],
    *,
    requested: Sequence[str] = (),
    scores: Optional[Mapping[str, float]] = None,
    load_order: Sequence[str] = (),
) -> List[str]:
    """*candidates* best first: requested, then relevance, then most recent load."""
    scores = scores or {}
    position = {name: i for i, name in enumerate(requested)}
    loaded_at = {name: i for i, name in enumerate(load_order)}

    def key(name: str) -> tuple:
        return (
            position.get(name, len(position)),
            -scores.get(name, float("-inf")),
            -loaded_at.get(name, -1),
            name,
        )

    return sorted(candidates, key=key)


def fit_bodies(
    ranked: Sequence[str], costs: Mapping[str, int], budget: int, fixed: int = 0
) -> Tuple[List[str], List[str], int]:
    """Split *ranked* into ``(full, summarized, tokens used)`` under *budget*.

    *fixed* is what always-on bodies already take. The first name is always
    full, whatever it costs: the most relevant skill's instructions are the
    point of loading it. Each later one is full if it still fits.
    """
    full: List[str] = []
    summarized: List[str] = []
    used = fixed
    for name in ranked:
        cost = costs.get(name, 0)
        if not full or used + cost <= budget:
            full.append(name)
            used += cost
        else:
            summarized.append(name)
    return full, summarized, used


def fit_skill_filter(
    agent: Any, skill_filter: Optional[List[str]], *, new_turn: bool = False
) -> Optional[List[str]]:
    """*skill_filter* narrowed to the bodies this turn's budget can show in full.

    ``None`` (every loaded body renders) stays ``None`` while everything fits;
    over budget it becomes the list of bodies shown in full. Explicit loads
    since the last turn began rank first; a turn's first call (*new_turn*)
    then forgets them, so mid-turn calls rank only the loads made since.

    No-op for an agent that has not opted in, and before its first turn.
    """
    if not enabled(agent):
        return skill_filter
    if not new_turn and getattr(agent, "_skill_budget", None) is None:
        return skill_filter
    skills = getattr(agent, "_loaded_skills", None) or {}
    always_on = frozenset(agent._always_on_skill_names)
    requested = list(getattr(agent, "_skill_requests", None) or ())
    if new_turn:
        agent._skill_requests = []
    window, source, budget = window_budget(agent)
    plan = SkillBudget(
        window=window,
        window_source=source,
        budget=budget,
        small_window=is_small_window(window),
        autoload_limit=SMALL_WINDOW_AUTOLOADS if is_small_window(window) else None,
    )
    selected = list(skills) if skill_filter is None else list(skill_filter)
    previous = getattr(agent, "_skill_budget", None)
    if not new_turn and skill_filter is not None and previous is not None:
        # Mid-turn the filter holds only the bodies shown in full; the ones
        # summarized this turn are still relevant, so they stay candidates.
        selected += [name for name in previous.summarized if name not in selected]
    candidates = [
        name
        for name in selected
        if name in skills and name not in always_on and skills[name].body
    ]
    costs = {name: body_tokens(skills[name]) for name in candidates}
    fixed = sum(body_tokens(skills[name]) for name in skills if name in always_on)
    plan.wanted_tokens = fixed + sum(costs.values())
    ranked = rank_skills(
        candidates,
        requested=requested,
        scores=_relevance(agent),
        load_order=list(skills),
    )
    if plan.wanted_tokens <= budget or len(candidates) < 2:
        plan.full = ranked
        plan.used_tokens = plan.wanted_tokens
        agent._skill_budget = plan
        if skill_filter is None or selected == list(skill_filter):
            return skill_filter
        return sorted(selected)
    plan.full, plan.summarized, plan.used_tokens = fit_bodies(
        ranked, costs, budget, fixed
    )
    agent._skill_budget = plan
    log.debug(
        "[skills] bodies over budget (~%d of %d tokens, window %s): full=%s "
        "summarized=%s",
        plan.wanted_tokens,
        budget,
        window,
        plan.full,
        plan.summarized,
    )
    kept = set(plan.full) | {name for name in selected if name not in candidates}
    return sorted(name for name in selected if name in kept)


def summarized_skills(agent: Any) -> Tuple[str, ...]:
    """Loaded skills summarized to fit this turn's budget."""
    plan = getattr(agent, "_skill_budget", None)
    if plan is None:
        return ()
    skills = getattr(agent, "_loaded_skills", None) or {}
    return tuple(name for name in plan.summarized if name in skills)


def render_key(agent: Any) -> tuple:
    """What decides the skills section: the filter and the summarized set."""
    current = getattr(agent, "_active_skill_filter", None)
    return (None if current is None else tuple(current), summarized_skills(agent))


def summary_line(skill: Any, description: str) -> str:
    """A summarized skill's line: its description and any reference files."""
    line = f"- {skill.name}: {description}"
    directory = getattr(skill, "directory", None)
    references = directory / "references" if directory is not None else None
    if references is None or not references.is_dir():
        return line
    files = sorted(p.name for p in references.iterdir() if p.is_file())
    if not files:
        return line
    shown = ", ".join(files[:SUMMARY_REFERENCES_SHOWN])
    more = len(files) - SUMMARY_REFERENCES_SHOWN
    if more > 0:
        shown += f" (+{more} more)"
    return f"{line} References in {references}: {shown}"


SUMMARY_HEADER = (
    "==== LOADED SKILLS (summarized to fit this model's context — call "
    "load_skill('<name>') again for one's full instructions, or read just the "
    "references/<file> you need) ===="
)


def withhold_hidden_skill_tools(
    agent: Any, tool_filter: Optional[List[str]]
) -> Optional[List[str]]:
    """*tool_filter* without the provided tools of skills not shown in full.

    Small windows only, and only a real filter: ``None`` sends the whole
    registry, which this never narrows.
    """
    plan = getattr(agent, "_skill_budget", None)
    if tool_filter is None or plan is None or not plan.small_window:
        return tool_filter
    skills = getattr(agent, "_loaded_skills", None) or {}
    active = agent._active_skill_names()
    hidden = sorted(
        skill.namespaced_tool_name(tool)
        for name, skill in skills.items()
        if active is not None and name not in active
        for tool in skill.tool_names
    )
    if hidden != plan.withheld_tools:
        if hidden:
            log.debug(
                "[skills] withholding tools of skills not shown in full "
                "(window %s): %s",
                plan.window,
                hidden,
            )
        plan.withheld_tools = hidden
    if not hidden:
        return tool_filter
    withheld = set(hidden)
    return [name for name in tool_filter if name not in withheld]


def skill_budget_report(agent: Any) -> Optional[Dict[str, Any]]:
    """This turn's skill budget for diagnostics, or ``None`` when not in use."""
    plan = getattr(agent, "_skill_budget", None)
    return None if plan is None else plan.as_dict()


def _relevance(agent: Any) -> Dict[str, float]:
    """This turn's relevance score per loaded skill, from the agent if it has one."""
    scores = getattr(agent, "_skill_relevance", None)
    return dict(scores()) if callable(scores) else {}


__all__ = [
    "SKILL_BUDGET_CEILING",
    "SKILL_BUDGET_FLOOR",
    "SKILL_BUDGET_FRACTION",
    "SMALL_WINDOW_AUTOLOADS",
    "SMALL_WINDOW_TOKENS",
    "SkillBudget",
    "body_tokens",
    "fit_bodies",
    "fit_skill_filter",
    "is_small_window",
    "note_skill_request",
    "rank_skills",
    "render_key",
    "resolve_skill_window",
    "skill_body_budget",
    "skill_budget_report",
    "summarized_skills",
    "summary_line",
    "withhold_hidden_skill_tools",
    "window_budget",
]
