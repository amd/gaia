---
name: finalize-implementation
description: >
  Run tests, lint, CI review simulation, fix issues in a loop, then commit and
  create a draft PR. Invoke when you believe the implementation is complete and
  ready for review. Runs inline to preserve full conversation context.
model: sonnet
disable-model-invocation: true
---

# Finalize Implementation

Validate the implementation against tests, lint, and the CI review checklist,
fix any issues found, then commit and open a draft PR.

## Prerequisites

Before starting, confirm:
- Working directory is clean or changes are ready to finalize
- You are on the correct feature branch (not `main`)

If on `main`, stop and ask the user which branch to use.

## Phase 1 — Baseline Verification

Run in order:

```bash
python -m pytest tests/unit/ -v --tb=short --cache-clear
```

```bash
python util/lint.py --all --fix
```

```bash
python util/lint.py --all
```

Record:
- Number of test failures (and which tests)
- Any lint violations remaining after `--fix`

If lint still fails after `--fix`, fix manually before proceeding.

## Phase 2 — CI Simulation

**Every iteration: read `.github/workflows/claude.yml` fresh — never use a
cached version.**

Steps:
1. Read `.github/workflows/claude.yml` and extract the `custom_instructions`
   from the `pr-review` job.
2. Run:
   ```bash
   git diff origin/main...HEAD
   git diff --name-status origin/main...HEAD
   ```
3. Review all changed files against the extracted checklist.
4. Produce a structured report:

```
## CI Review Report — Iteration N

### 🔴 Critical
- [issue] (file:line)

### 🟡 Important
- [issue] (file:line)

### 🟢 Minor
- [issue] (file:line)
```

Severity definitions (from the CI checklist):
- 🔴 Critical — security vulnerabilities, breaking changes, data loss risks
- 🟡 Important — bugs, architectural concerns, missing tests, missing docs
- 🟢 Minor — style, optimizations, non-blocking suggestions

## Phase 3 — Remediation Loop

**Hard cap: 5 iterations total** (Phase 1 + Phase 2 = one iteration).

Each iteration:
1. Fix 🔴 issues first, then 🟡 issues. Skip 🟢 unless trivial.
2. Re-run Phase 1 (tests + lint with `--cache-clear`).
3. Re-run Phase 2 (fresh `.github/workflows/claude.yml` read every time).
4. Evaluate exit conditions.

### Exit Conditions

**Exit normally (proceed to Phase 4) when:**
- Zero 🔴 and zero 🟡 issues remain AND all tests pass AND lint is clean

**Exit with escalation (stop and report to user) when:**
- 5 iterations reached and issues remain — report what's left and ask for guidance
- The same 🔴 or 🟡 issue appears unchanged in 2 consecutive iterations — you
  are stuck; report it immediately rather than continuing

**Never silently skip a 🔴 issue to reach the exit condition.**

## Phase 4 — Final Validation

### 4a. Intent Check

Using the full conversation context (this skill runs inline), verify:
- The implementation matches what the user originally asked for
- No scope creep was introduced during remediation
- Nothing from the original request was accidentally dropped

If there is a mismatch, fix it and re-run Phase 1 before continuing.

### 4b. Sub-agent Reviews

Launch both agents in parallel:

```
Agent: code-reviewer
Prompt: Review all files changed in this branch (git diff origin/main...HEAD)
for bugs, logic errors, security issues, and GAIA/AMD compliance.
Report 🔴 Critical, 🟡 Important, 🟢 Minor issues only.
```

```
Agent: architecture-reviewer
Prompt: Review all files changed in this branch (git diff origin/main...HEAD)
for SOLID principles, proper layering, dependency hygiene, and architectural
consistency with the existing GAIA codebase.
Report 🔴 Critical, 🟡 Important, 🟢 Minor issues only.
```

If either reviewer finds 🔴 or 🟡 issues, return to Phase 3 (counts against
the 5-iteration cap).

### 4c. Commit and PR

Once clean, invoke:

```
Skill: commit-commands:commit-push-pr
```

The PR must:
- Be created as a **draft**
- Title derived from the branch name or original issue title
- Body includes a link to the GitHub issue (if one was mentioned in the
  conversation) using `Closes #NNN` or `Relates to #NNN`

## Output

After completion, print a summary table:

```
## Finalize Implementation — Complete

| Step              | Result                        |
|-------------------|-------------------------------|
| Iterations used   | N / 5                         |
| Tests             | ✅ Passing / ❌ N failures     |
| Lint              | ✅ Clean / ❌ Violations       |
| 🔴 Issues         | 0 resolved, 0 remaining       |
| 🟡 Issues         | N resolved, 0 remaining       |
| 🟢 Issues         | N noted (not blocking)        |
| PR                | <URL or "Not created">        |
```

If the loop exited early due to the iteration cap or a stuck issue, replace the
PR row with a clear description of what blocked completion and what the user
should do next.
