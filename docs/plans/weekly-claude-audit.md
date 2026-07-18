# Weekly Claude deep-audit → triage issue + human-gated PRs

Design record for `.github/workflows/claude-weekly-audit.yml`.

## Why this matters

Every Claude job in this repo is **reactive** — it fires on a PR, a comment, or an
`@claude` mention (`claude.yml`). Nothing looks at the repo *proactively*, so debt that
ships inside a green PR — a feature with no doc page, a code path with no test, a silent
`except Exception: pass`, a `cli.mdx` that drifted from the real flags — sits unnoticed
until a user hits it. This workflow adds the missing proactive lens: a scheduled deep
review that files **one ranked triage issue** a maintainer skims and promotes.

**Human-gated, not an auto-merge bot.** The workflow produces *findings*, never commits.

## Modes

- **normal** (weekly) — reviews the last N days of merged work + the subsystems it touched.
- **deep** (monthly) — whole-codebase latent-debt sweep. Auto-selected on the first
  Monday of each month (day-of-month ≤ 7, since cron only fires Mondays); also selectable
  via `workflow_dispatch`. Deep runs still file issues — that is the point of the sweep.

## Dimensions (one read-only Claude job each, in parallel)

| Dimension | Looks for |
|-----------|-----------|
| `security` | injection, unsafe subprocess/eval, unsafe deserialization, secret handling, path traversal, fork-PR surface — **never posts detail publicly** (see below) |
| `correctness` | code that is **wired but misbehaves** — a handler that flips a success flag without doing the work (a rollback that never rolls back), a poller that always returns null, a mode that no-ops, a flag whose handler raises `NotImplementedError` — plus real logic bugs and every CLAUDE.md "Fail Loudly" violation. **Owns wired-but-broken behavior and the silent-fallback check.** |
| `docs` | new feature with no `docs/` page or `docs.json` entry; `cli.mdx` drift; `amd-gaia.ai` links missing `/docs/`; hub-agent README/SPEC/SKILL/CHANGELOG drift. A feature **documented as working but stubbed** is a docs finding (doc-vs-code drift), not features. |
| `tests` | code paths with no test, or assertions that prove invocation not call validity (#1655). In **deep** mode, plain "module X has no coverage" rolls up into ONE aggregate finding; separate findings only for risk-bearing untested logic (auth/gate/precedence/error-mapping/#1655). |
| `features` | a genuinely **missing or half-shipped** capability where nothing is wired yet (a TODO standing in for unwritten code). If the code is wired but broken, that is `correctness`, not features. |

The five lenses are **mutually exclusive** — the decisive question for a broken thing is
*is the code wired but misbehaving (correctness), never written (features), or contradicted
by its docs (docs)?* Without that boundary, correctness findings leak into features and the
priciest job's output disappears.

### Published hub agents — the highest bar

Published agents are the project's shop window: an integrator installs them and judges GAIA
by them. In **both** modes, each lens double-checks any published agent explicitly and
**bumps a gap up one severity** (never 🟡; a default-path break is 🔴). Published agents are
detected by a `release_agent_<id>.yml` workflow, a shipped `SCORECARD.md`, or a released
`version:` in `gaia-agent.yaml` — currently the **email agent** (`hub/agents/python/email/`
+ `hub/agents/npm/agent-email/`). The bar, per lens:

- **docs** — README (integrator-facing, high quality), `SPEC.md` (full reference), `SKILL.md`
  (AI-integration playbook), `CHANGELOG.md`, and any shipped contract spec, all present,
  mutually consistent, and genuinely written; a `SCORECARD.md` exists and is linked from the README.
- **tests** — solid unit + integration coverage of the real request/response contract
  (#1655, not mock-only); the `SCORECARD.md` comes from a real eval and passes
  `gaia.eval.scorecard_gate` (never hand-authored).
- **correctness** — runtime code is bulletproof: no stubs, no silent fallbacks, no
  half-finished paths, actionable errors at every boundary.
- **security** — strictest reading (agent code runs on integrators' machines).

> **Fix vs. the original handoff:** the first draft had four dimensions
> (security/tests/docs/features) and listed the silent-fallback check in the scope
> narrative but assigned it to **no** dimension — it would have fallen through. This
> design adds a fifth **`correctness`** dimension and gives it that check explicitly, so
> the workflow covers code *correctness* (bugs) as well as code *debt* (tests, features).

## Synthesis → one triage issue + child issues

A synthesis job collects the five structured outputs, reduces them to the verified new set,
ranks by severity (**🔴 high · 🟠 medium · 🟡 low** — no green; green reads as "pass"), and files:

- **Parent triage issue** (`weekly-audit`): opens with a one-line tally (new/low/suppressed
  counts) for trend, then a section for **every** dimension with a finding, in fixed order
  (Security, Correctness, Features, Docs, Tests), each finding grouped under the dimension it
  *declares* (never re-bucketed). Each run **supersedes and closes the previous parent** so
  they don't pile up.
- **Per-finding child issues** for **🔴/🟠 only** — 🟡 (low) findings are listed in the
  parent, not filed separately, to cap churn. Each child carries its `evidence` and an
  **auto-fixable** flag; a maintainer promotes one with **`bug`** (→ existing `auto-fix`
  job), or permanently silences it with **`audit-wontfix`**. No new PR-creation code.

**Precision & lifecycle** (what makes it trustworthy over time):
- **Verify before file**: dimensions must carry `evidence` (a read quote) and confirm the
  problem isn't already handled; synthesis drops 🔴/🟠 findings whose evidence is thin.
- **Cross-dimension dedup within a run**: one root cause = one issue, even if two lenses flag it.
- **Permanent suppression**: a finding whose key sits on an `audit-wontfix` issue never re-files.

## Invariants (see the `weekly-audit-patterns` skill)

- **Dedup key** = `<dimension>:<path>:<symbol-or-section>` — a function/class name or doc
  heading, **never a line number** (line numbers move and re-file the finding every week).
  Embedded as `<!-- audit-key: KEY -->` in each child body; synthesis skips any key already
  on an *open* `weekly-audit` issue OR on any `audit-wontfix` issue (accepted debt).
- **Security stays private**: full detail to the job run log only; a redacted stub in
  `findings-security.json`; the public issue shows a count + run-log pointer + `@kovtcharov-amd`.
- **Skip-if-empty**: normal mode exits before any Claude call on a no-change week.
- **Read-only**: `--allowedTools Read,Grep,Glob,Bash`; never install or run repo code.
- **Model** `claude-opus-4-8` via the top-level `AUDIT_MODEL` env (one place to change);
  `claude-fable-5` for max depth at ~2x cost. Dimensions run `max-parallel: 1` (serialized)
  to stay under the Max subscription's rolling rate limit.

## Non-goals

- ❌ Auto-merging anything, or opening PRs directly (promotion via `bug` → `auto-fix`).
- ❌ Running `gaia eval agent` / Lemonade-dependent checks (this is static analysis).
  Execution-based verification now lives in the sibling workflow
  `.github/workflows/claude-weekly-doc-walkthrough.yml` — see
  `docs/plans/weekly-doc-walkthrough-audit.md`. That workflow exists precisely because
  this one's static-only scope let #2260 and #2261 both ship undetected.
- ❌ Replacing per-PR review — it complements `claude.yml`, doesn't duplicate it.
