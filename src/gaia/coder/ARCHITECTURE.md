# ARCHITECTURE.md — Composition map for gaia-coder

> Phase 1 placeholder. Per §6.5 of `docs/plans/coder-agent.mdx` this file
> describes **how she is composed** — mixins, state machine, tool registry,
> invariants. It is injected into every system prompt alongside `GAIA.md` and
> is expected to mutate as her composition changes. Phase 3+ wires the
> auto-regenerated sections (Surface, Mixin stack, State machine); Phase 1
> ships the skeleton so downstream tasks have a stable file to read and
> edit.

## Surface

Auto-generated from `introspect_tool_registry` once the introspection
mixin lands in Phase 3. Holds one row per publicly-registered tool: name,
mixin source, signature, atomic flag.

## Mixin stack

Auto-generated from `introspect_mixin_stack`. Holds the MRO of the running
`CoderAgent` subclass, each mixin's file path, and a one-line
responsibility statement.

## State machine

A Mermaid render of the current ReAct graph, produced by
`gaia.coder.loop.introspect_state_machine`. The stock v1 graph has 20
states grouped into the 7 stages defined in §5.1 of the spec. The graph
is editable per §7.8 — every merged edit bumps `Loop.version` and records
the before/after diagram in the PR body.

## Invariants

Rules she must preserve, each citing the spec section that defines it:

- Fail loudly — never a silent fallback (repo `CLAUDE.md`; §2 principle 3).
- Never push to `main`; she integrates on the `code` branch (§4.2, §5.7).
- Every change is covered by at least one test (`declare_done` gate, §2
  principle 5).
- Seven review passes before every PR (§8).
- Self-edit is gated by dev mode AND EM opt-in (§2 principle 6, §7.1).

## Open questions

Things she knows she does not yet know about her own design. Phase 1
seeds this list with: "loop runner not yet implemented", "introspection
mixin is a stub", "memory stores are in a sibling branch".

## Change log

Append-only. One line per merged self-edit PR with PR link, fix-class,
and `loop_version` before/after. Seeded in Phase 1 with the scaffolding
PR itself.
