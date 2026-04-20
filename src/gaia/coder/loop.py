# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The editable ReAct state machine for gaia-coder.

This module is the canonical source of truth for the agent's default control
flow. It implements the schema and default loop defined in
``docs/plans/coder-agent.mdx`` §15.3.

Three immutable dataclasses model the graph:

* :class:`Transition` — an edge: target state, guard predicate, optional
  side-effect, and a human-readable label used in Mermaid renders.
* :class:`State` — a node: name, stage grouping, optional enter/exit hooks,
  outbound transitions, memory-topic hooks, and two flags controlling
  breakpoint polling and continuous-critique emission.
* :class:`Loop` — the whole graph: a version integer (bumped on every merged
  state-machine edit per §7.8), the ordered tuple of states, the entry
  state, and the terminal state(s).

:data:`DEFAULT_LOOP` is the stock 20-state loop grouped into the seven
stages (Intake, Understand, Design, Build, Verify, Publish, Land) described
in §5.1. It is the default the daemon runs with. Every merged edit to this
file bumps ``Loop.version`` so audit-log entries can be traced back to the
exact loop edition they ran under.

The loop is **editable**: per §7.8 the agent herself can propose source
edits to this file (under strict guardrails). That mutability is why the
graph is declarative and the dataclasses are frozen — an edit means a diff
to this file, not a runtime mutation API.

A minimal :class:`LoopContext` type is declared here as a forward-reference
stub. The real context lives alongside the runtime loop runner, which is a
Phase 3 deliverable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

# ---------------------------------------------------------------------------
# Context (forward-reference stub)
# ---------------------------------------------------------------------------


class LoopContext:
    """Forward-reference placeholder for the runtime loop context.

    The real :class:`LoopContext` — the mutable per-turn scratch space that
    a state's ``on_enter`` / ``on_exit`` hooks and a transition's ``when``
    guard consult — is implemented in a later phase alongside the loop
    runner. It exposes attributes such as ``needs_clarification``,
    ``task.class_``, ``em_approved_plan``, ``failure_is_complex``, etc.

    Declaring it here as a bare class keeps type annotations below
    resolvable without pulling the runner into this module.
    """


# ---------------------------------------------------------------------------
# Stage labels
# ---------------------------------------------------------------------------

Stage = Literal[
    "Intake",
    "Understand",
    "Design",
    "Build",
    "Verify",
    "Publish",
    "Land",
]


# ---------------------------------------------------------------------------
# Immutable graph dataclasses (§15.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Transition:
    """A directed edge in the ReAct graph.

    Attributes:
        to: Target state name. Must match some :attr:`State.name` in the
            parent :class:`Loop`.
        when: Guard predicate. Called with the active :class:`LoopContext`;
            the transition is taken when it returns ``True``. Guards are
            evaluated in declaration order and the first match wins.
        action: Optional side-effect invoked on traversal (logging, memory
            writes, etc.). Kept separate from ``when`` so guards stay pure.
        label: Human-readable label used when rendering the graph as
            Mermaid for introspection.
    """

    to: str
    when: Callable[[LoopContext], bool]
    action: Optional[Callable[[LoopContext], None]] = None
    label: str = ""


@dataclass(frozen=True)
class State:
    """A node in the ReAct graph.

    Attributes:
        name: Unique state identifier.
        stage: One of the seven stage labels. Stage transitions are
            natural breakpoints (§4.5).
        on_enter: Optional hook run before outbound transitions are
            evaluated.
        on_exit: Optional hook run after the chosen transition is picked
            but before control moves to the target state.
        transitions: Tuple of outbound transitions. Order is significant:
            guards evaluate in this order and the first ``True`` wins.
        memory_read: Memory topic names to query on enter (§6.8 hooks).
        memory_write: Memory topic names to write on exit.
        is_breakpoint: When ``True``, the EM inbox is polled and the
            heartbeat cost is metered on entry. Stage boundaries and
            user-facing gates are breakpoints.
        emit_critique: When ``True`` (default), the continuous-critique
            pass from §7.2 runs after this state exits. Turned off for
            pure bookkeeping states such as ``boot``.
    """

    name: str
    stage: Stage
    on_enter: Optional[Callable[[LoopContext], None]] = None
    on_exit: Optional[Callable[[LoopContext], None]] = None
    transitions: tuple[Transition, ...] = ()
    memory_read: tuple[str, ...] = ()
    memory_write: tuple[str, ...] = ()
    is_breakpoint: bool = False
    emit_critique: bool = True


@dataclass(frozen=True)
class Loop:
    """The declarative ReAct graph.

    Attributes:
        version: Monotonically-increasing integer bumped on every merged
            state-machine edit (§7.8). Audit-log rows record the
            ``loop_version`` they ran under so behaviour regressions can
            be traced back to the exact loop edition.
        states: Ordered tuple of all states in the graph.
        entry_state: Name of the start state. Defaults to ``"boot"``.
        terminal_states: Names of the terminal states. Defaults to
            ``("end",)``.
    """

    version: int
    states: tuple[State, ...]
    entry_state: str = "boot"
    terminal_states: tuple[str, ...] = ("end",)

    def state_by_name(self, name: str) -> State:
        """Return the :class:`State` with the given ``name``.

        Raises:
            KeyError: If no state with that name exists. Fail-loudly per
                the repo ``CLAUDE.md`` rule.
        """
        for state in self.states:
            if state.name == name:
                return state
        raise KeyError(
            f"No state named {name!r} in loop version {self.version}. "
            f"Known states: {[s.name for s in self.states]}"
        )


# ---------------------------------------------------------------------------
# Default loop — 20 states grouped into 7 stages (§5.1, §15.3)
# ---------------------------------------------------------------------------

# Guards are written as lambdas reading well-known attributes from the
# runtime :class:`LoopContext`. The context is a forward reference until
# Phase 3 wires the runner.

DEFAULT_LOOP: Loop = Loop(
    version=1,
    states=(
        # ------------------------------------------------------------------
        # Stage 1 — Intake
        # ------------------------------------------------------------------
        State(
            name="boot",
            stage="Intake",
            is_breakpoint=False,
            emit_critique=False,
            transitions=(
                Transition(to="triage", when=lambda c: True, label="boot complete"),
            ),
        ),
        State(
            name="triage",
            stage="Intake",
            is_breakpoint=True,
            memory_read=("failure_patterns", "task_outcomes", "adr_decisions"),
            transitions=(
                Transition(
                    to="clarify",
                    when=lambda c: c.needs_clarification,
                    label="ambiguous",
                ),
                Transition(
                    to="explore",
                    when=lambda c: c.task.class_ in ("architectural", "feature"),
                    label="broad scope",
                ),
                Transition(
                    to="localise",
                    when=lambda c: c.task.class_ in ("tool", "test", "prompt", "doc"),
                    label="narrow fix",
                ),
                Transition(
                    to="debug",
                    when=lambda c: c.task.kind == "bug",
                    label="bug report",
                ),
            ),
        ),
        State(
            name="clarify",
            stage="Intake",
            is_breakpoint=True,
            transitions=(
                Transition(
                    to="triage",
                    when=lambda c: c.em_answered,
                    label="EM answered",
                ),
            ),
        ),
        # ------------------------------------------------------------------
        # Stage 2 — Understand
        # ------------------------------------------------------------------
        State(
            name="explore",
            stage="Understand",
            is_breakpoint=True,
            memory_read=("adr_decisions",),
            transitions=(
                Transition(
                    to="plan_draft",
                    when=lambda c: c.explored,
                    label="explored",
                ),
            ),
        ),
        State(
            name="localise",
            stage="Understand",
            is_breakpoint=False,
            memory_read=("review_patterns", "adr_decisions"),
            transitions=(
                Transition(
                    to="plan_draft",
                    when=lambda c: c.localised,
                    label="localised",
                ),
            ),
        ),
        # ------------------------------------------------------------------
        # Stage 3 — Design
        # ------------------------------------------------------------------
        State(
            name="plan_draft",
            stage="Design",
            is_breakpoint=False,
            memory_read=("em_preferences", "adr_decisions"),
            transitions=(
                Transition(
                    to="plan_review",
                    when=lambda c: c.is_large_job,
                    label="large job -> review",
                ),
                Transition(
                    to="test_first",
                    when=lambda c: not c.is_large_job,
                    label="small job -> build",
                ),
            ),
        ),
        State(
            name="plan_review",
            stage="Design",
            is_breakpoint=True,
            transitions=(
                Transition(
                    to="test_first",
                    when=lambda c: c.em_approved_plan,
                    label="EM approved",
                ),
                Transition(
                    to="plan_refine",
                    when=lambda c: c.em_requested_changes,
                    label="EM requested changes",
                ),
            ),
        ),
        State(
            name="plan_refine",
            stage="Design",
            is_breakpoint=False,
            transitions=(
                Transition(
                    to="plan_draft",
                    when=lambda c: c.refinement_rounds < 3,
                    label="refine (<3 rounds)",
                ),
            ),
        ),
        # ------------------------------------------------------------------
        # Stage 4 — Build
        # ------------------------------------------------------------------
        State(
            name="test_first",
            stage="Build",
            is_breakpoint=False,
            transitions=(
                Transition(
                    to="edit",
                    when=lambda c: c.regression_test_written,
                    label="test written",
                ),
            ),
        ),
        State(
            name="edit",
            stage="Build",
            is_breakpoint=False,
            transitions=(
                Transition(
                    to="verify_local",
                    when=lambda c: c.edit_complete,
                    label="edit complete",
                ),
            ),
        ),
        # ------------------------------------------------------------------
        # Stage 5 — Verify
        # ------------------------------------------------------------------
        State(
            name="verify_local",
            stage="Verify",
            is_breakpoint=False,
            transitions=(
                Transition(
                    to="debug",
                    when=lambda c: c.local_verification_failed,
                    label="verification failed",
                ),
                Transition(
                    to="self_review",
                    when=lambda c: c.local_verification_passed,
                    label="verification passed",
                ),
            ),
        ),
        State(
            name="debug",
            stage="Verify",
            is_breakpoint=True,
            memory_read=("failure_patterns", "flaky_tests"),
            memory_write=("failure_patterns",),
            transitions=(
                Transition(
                    to="edit",
                    when=lambda c: c.fix_candidate_ready,
                    label="fix ready",
                ),
                Transition(
                    to="plan_draft",
                    when=lambda c: c.requires_replan,
                    label="needs replan",
                ),
            ),
        ),
        State(
            name="self_review",
            stage="Verify",
            is_breakpoint=False,
            memory_read=("review_patterns", "mutation_seeds"),
            transitions=(
                # Publish if every review pass is green (§8).
                Transition(
                    to="publish",
                    when=lambda c: c.all_passes_green,
                    label="all passes green",
                ),
                # A complex failure warrants the dedicated debug sub-loop
                # (§5.9) — root cause is deeper than a surface edit.
                Transition(
                    to="debug",
                    when=lambda c: c.any_pass_failed and c.failure_is_complex,
                    label="complex failure -> debug",
                ),
                # A shallow failure goes straight back to edit for a
                # surgical fix without re-entering the full debug loop.
                Transition(
                    to="edit",
                    when=lambda c: c.any_pass_failed and not c.failure_is_complex,
                    label="shallow failure -> edit",
                ),
            ),
        ),
        # ------------------------------------------------------------------
        # Stage 6 — Publish & iterate
        # ------------------------------------------------------------------
        State(
            name="publish",
            stage="Publish",
            is_breakpoint=True,
            transitions=(
                Transition(
                    to="notify",
                    when=lambda c: c.pr_opened,
                    label="PR opened",
                ),
            ),
        ),
        State(
            name="notify",
            stage="Publish",
            is_breakpoint=False,
            transitions=(Transition(to="wait", when=lambda c: True, label="notified"),),
        ),
        State(
            name="wait",
            stage="Publish",
            is_breakpoint=True,
            transitions=(
                Transition(
                    to="revise",
                    when=lambda c: c.review_requested_changes,
                    label="review requested changes",
                ),
                Transition(
                    to="verify_merge",
                    when=lambda c: c.pr_merged,
                    label="PR merged",
                ),
                Transition(
                    to="end",
                    when=lambda c: c.pr_rejected,
                    label="PR rejected",
                ),
            ),
        ),
        State(
            name="revise",
            stage="Publish",
            is_breakpoint=False,
            transitions=(
                Transition(to="edit", when=lambda c: True, label="loop back to edit"),
            ),
        ),
        # ------------------------------------------------------------------
        # Stage 7 — Land & learn
        # ------------------------------------------------------------------
        State(
            name="verify_merge",
            stage="Land",
            is_breakpoint=False,
            memory_write=("review_patterns", "failure_patterns", "task_outcomes"),
            transitions=(
                Transition(to="learn", when=lambda c: True, label="merge verified"),
            ),
        ),
        State(
            name="learn",
            stage="Land",
            is_breakpoint=False,
            transitions=(Transition(to="end", when=lambda c: True, label="learned"),),
        ),
        State(
            name="end",
            stage="Land",
            is_breakpoint=False,
        ),
    ),
)


# ---------------------------------------------------------------------------
# Introspection helpers (§7.7)
# ---------------------------------------------------------------------------


def _mermaid(loop: Loop) -> str:
    """Render ``loop`` as a Mermaid ``stateDiagram-v2`` string.

    The ``stateDiagram-v2`` dialect is chosen over ``graph TD`` because it
    groups states into ``state <Stage> { ... }`` blocks, which matches the
    seven-stage grouping from §5.1 and makes the rendered diagram far more
    readable in the Mermaid preview that ``gaia-coder introspect`` will
    surface.
    """
    lines: list[str] = ["stateDiagram-v2"]

    # Group states by stage, preserving encounter order inside each stage.
    stages: dict[str, list[State]] = {}
    stage_order: list[str] = []
    for state in loop.states:
        if state.stage not in stages:
            stages[state.stage] = []
            stage_order.append(state.stage)
        stages[state.stage].append(state)

    # Entry arrow.
    lines.append(f"    [*] --> {loop.entry_state}")

    # Nested state blocks per stage (renders as a labelled container).
    for stage in stage_order:
        block_name = f"Stage_{stage}"
        lines.append(f'    state "{stage}" as {block_name} {{')
        for state in stages[stage]:
            if state.is_breakpoint:
                lines.append(f"        {state.name} : breakpoint")
            else:
                lines.append(f"        {state.name}")
        lines.append("    }")

    # Transitions.
    for state in loop.states:
        for transition in state.transitions:
            label = transition.label or ""
            if label:
                lines.append(f"    {state.name} --> {transition.to} : {label}")
            else:
                lines.append(f"    {state.name} --> {transition.to}")

    # Terminal arrows.
    for terminal in loop.terminal_states:
        lines.append(f"    {terminal} --> [*]")

    return "\n".join(lines)


def introspect_state_machine(loop: Loop = DEFAULT_LOOP) -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of the ReAct loop plus a Mermaid render.

    This is the ``loop``-flavoured introspection helper described in §7.7.
    The ``IntrospectionToolsMixin`` (Phase 3) will wrap this as a
    ``@tool``-decorated public tool; exposing it as a plain function here
    keeps the canonical implementation next to the loop it describes and
    lets the sibling ``introspect/`` package re-export without duplicating
    logic.

    Args:
        loop: The loop to snapshot. Defaults to :data:`DEFAULT_LOOP`.

    Returns:
        A dict with two top-level keys:

        * ``json``: a nested dict describing the loop (version, entry,
          terminals, and per-state name / stage / flags / transitions).
        * ``mermaid``: a ``stateDiagram-v2`` render — the string always
          contains the literal substring ``stateDiagram`` so introspection
          callers can assert the render shape.
    """
    states_payload: list[dict[str, Any]] = []
    for state in loop.states:
        states_payload.append(
            {
                "name": state.name,
                "stage": state.stage,
                "is_breakpoint": state.is_breakpoint,
                "emit_critique": state.emit_critique,
                "memory_read": list(state.memory_read),
                "memory_write": list(state.memory_write),
                "transitions": [
                    {"to": t.to, "label": t.label} for t in state.transitions
                ],
            }
        )

    payload: dict[str, Any] = {
        "version": loop.version,
        "entry_state": loop.entry_state,
        "terminal_states": list(loop.terminal_states),
        "states": states_payload,
    }

    # Round-trip through ``json.dumps`` -> ``json.loads`` so the caller
    # knows the payload is definitely JSON-serialisable (no stray
    # callables leaking through).
    json_payload = json.loads(json.dumps(payload))

    return {
        "json": json_payload,
        "mermaid": _mermaid(loop),
    }


__all__ = [
    "DEFAULT_LOOP",
    "Loop",
    "LoopContext",
    "Stage",
    "State",
    "Transition",
    "introspect_state_machine",
]
