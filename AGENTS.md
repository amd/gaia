# AGENTS.md — Rules for coding agents working on GAIA

This file establishes how coding agents (Claude Code, Cursor, Copilot, custom orchestrators) coordinate when contributing to GAIA. It complements `CLAUDE.md` (which covers project conventions) by adding **multi-agent coordination rules**.

If you are a human contributor, you don't need to follow these rules — but you should read them to understand how parallel agent work is being managed.

If you are a coding agent, **read this file before opening any PR**.

---

## Priority order

User instructions override everything. Then:

1. **User explicit instructions** (CLAUDE.md, this file, direct chat instructions)
2. **`CLAUDE.md` project conventions** (commit policy, no-silent-fallback rule, attribution rules)
3. **This file (AGENTS.md)** — multi-agent coordination
4. **Default agent / model behavior**

If something here conflicts with `CLAUDE.md`, `CLAUDE.md` wins.

---

## Pre-flight checks before opening a PR

### 1. Check for stop-the-line PRs
```bash
gh pr list --repo amd/gaia --label "stop-the-line"
```

If any active stop-the-line PRs exist, read their pinned "frozen paths" comments. If your work touches a frozen path, either (a) wait, or (b) carve out the non-frozen subset of your work to ship first. Do NOT merge changes that conflict with foundational PRs in flight.

### 2. Check what other agents are working on
If running under Claudia or another orchestrator:
```
claudia_list_tasks
```
Confirm no other agent is working on the same issue or touching the same files. If overlap exists, coordinate via the orchestrator before proceeding.

### 3. Verify spec-readiness for consumer-critical issues
Before starting implementation on a `consumer-critical` issue, confirm it has the `spec-ready` label. If not, the issue lacks the implementation depth needed for clean agent execution — write or request a spec first (see issue #899 / A2 for the pre-flight spec process).

### 4. Read the issue carefully + adjacent issues
- Open every cross-referenced issue
- Read recent comments — scope often evolves there
- Check the issue's milestone description for release theme alignment

---

## Coordination rules

### Stop-the-line (foundational PRs in flight)
When a foundational PR (large, cross-cutting, with many downstream dependencies) is in flight, it carries the `stop-the-line` label and pins a comment listing **frozen file paths**. No PRs may merge changes to those paths until the stop-the-line PR lands.

### Parallel agent work
- **Parallelize when** file trees are disjoint, no architectural decision is shared, no sequential dependency exists
- **Serialize when** the same file tree is touched, when a design-system pattern needs to be pinned first, when one PR's output is the next PR's input
- **One agent per issue** — don't have two agents working the same issue concurrently

### Stop-the-line for design-system decisions
Mobile UI work (M1/M2/M3/M5) and any cross-cutting visual/UX work requires a pinned design-system spec before agent assignment. See issue #900 (A3) for the mobile design-system spec process.

---

## Quality gates

### Tests are mandatory, not optional
Every PR must include:
- Unit tests for new logic
- Integration tests for new external surfaces (API endpoints, MCP tools, CLI subcommands)
- No silent test skips — `pytest.skip("X not yet implemented")` is forbidden in this codebase (per `CLAUDE.md` no-silent-fallback rule)
- Failures must surface — never `|| true` in CI commands

### Review chain
Every agent-authored PR runs through:
1. **`code-reviewer` agent** (`.claude/agents/code-reviewer.md`) — quality, framework compliance, AMD requirements
2. **`architecture-reviewer` agent** (`.claude/agents/architecture-reviewer.md`) — for PRs touching base classes, agent framework, or cross-module patterns
3. **`claude.yml` workflow Opus reviewer** — final automated pass
4. **Human review** — for integration, UX coherence, "does this solve the problem"

A PR may not merge until 1 + 4 are green. 2 is required for architecture-touching PRs. 3 is automatic.

### Spec-before-PR for consumer-critical work
Issues with the `consumer-critical` label require an implementation spec at the depth of #887/#888/#890 (see those issues' pinned comments) before agent assignment. The spec includes:
- Dataclasses / interfaces
- Pseudocode for non-obvious algorithms
- Acceptance criteria mapped to verifiable tests
- Failure modes and fail-loudly behavior
- Attribution / prior art for non-obvious patterns

Write specs by reading the issue + adjacent code + linked issues; post as a comment on the original issue; tag with `spec-ready`.

---

## What agents must NOT do

- ❌ Open PRs that touch frozen paths during a stop-the-line freeze
- ❌ Merge PRs without passing the review chain above
- ❌ Add silent fallbacks (`except Exception: pass`, fallback model glue, swallowed errors) — see `CLAUDE.md`
- ❌ Add Claude attribution (no `Co-Authored-By: Claude`, no `Generated with Claude Code` taglines) — see `CLAUDE.md`
- ❌ Skip tests with vague reasons (`pytest.skip("not yet implemented")` while shipping the implementation)
- ❌ Reformat unrelated files (scope creep — see `CLAUDE.md` "scope-clean" rule)
- ❌ Push to remote without explicit user instruction
- ❌ Force-push, amend commits, or rewrite shared history

---

## Working inside Claudia (multi-agent orchestrator)

If you are running as a Claudia task:

- Always call `claudia_list_tasks` before starting work — avoid duplicate effort
- Title your task descriptively via `claudia_rename_task` (3-6 words)
- Spawn parallel sub-tasks for genuinely independent work via `claudia_create_task`
- Each spawned task prompt must be **self-contained** — include file paths, context, and constraints
- Monitor via `claudia_get_task_status`; integrate via `claudia_get_task_output`
- Don't `claudia_stop_all_tasks` without user confirmation

For one-shot research or implementation that doesn't need parallel work, call agents directly via the `Agent` tool instead.

---

## Issue scoping for agent execution

When breaking work into issues for agents:

### Good agent issues
- Single file tree (no cross-cutting touches)
- Concrete acceptance criteria mapping to verifiable tests
- Spec at the depth of #887/#888/#890 (for consumer-critical) or simpler issues for non-critical
- Explicit list of files to be created or modified
- Attribution / prior-art references where applicable
- Explicit "out of scope" section

### Bad agent issues
- "Improve X" with no measurable definition of done
- Multiple unrelated changes bundled into one issue
- Hidden architectural decisions ("agent should pick the right approach")
- Cross-PR coordination implicit in the body but not called out

When in doubt, file a smaller issue.

---

## Release validation

Before any release ships, run the consumer journey end-to-end (see issue A4 / consumer-launch integration validation for v0.20.0). Per-PR tests are not a substitute. The orchestrator or release manager runs this — not the agents who wrote the individual PRs (calibration risk).

---

## Where to find things

- **Project conventions:** `CLAUDE.md`
- **Roadmap:** [`docs/roadmap.mdx`](docs/roadmap.mdx)
- **Agent definitions:** `.claude/agents/*.md`
- **Orchestration playbook:** `docs/playbooks/agent-orchestration.mdx` (see issue #A1)
- **Mobile design-system spec:** `docs/spec/mobile-design-system.md` (see issue #A3)
- **Currently active stop-the-line PRs:** `gh pr list --repo amd/gaia --label stop-the-line`
- **Consumer-critical issue tracker:** `gh issue list --repo amd/gaia --label consumer-critical`
- **Spec-ready issues (agent-assignable):** `gh issue list --repo amd/gaia --label spec-ready`

---

## Updating this file

This file evolves. After each release, the orchestrator updates it with lessons learned (what worked, what didn't, what new rules are needed). Treat changes to AGENTS.md the same as changes to architectural docs — `architecture-reviewer` should review.
