---
name: "weekly-audit-patterns"
description: "The non-obvious invariants of the proactive weekly Claude audit workflow (.github/workflows/claude-weekly-audit.yml): the stable dedup-key scheme that keeps it from re-filing findings every week, the private channel for security findings, the five audit dimensions and which one owns the Fail-Loudly check, and the `bug`-label → auto-fix promotion path. Read before editing that workflow, changing how findings are filed/deduped, or adding an audit dimension."
---

# Weekly Audit Patterns

`.github/workflows/claude-weekly-audit.yml` is the repo's one **proactive** Claude
lens — a scheduled deep review (not triggered by a PR) that fans out one read-only
Claude job per dimension and files a ranked triage issue. Everything else in
`claude.yml` is reactive. These are the invariants a future editor will otherwise break.

## The five dimensions are mutually exclusive

`security`, `correctness`, `docs`, `tests`, `features` — a matrix of one Claude job each.
The lenses **overlap unless the prompt keeps them disjoint**, and the first run proved it:
correctness findings (a rollback that never rolls back, a poller returning null, a mode
that no-ops) leaked into `features`, and the priciest job's output vanished from the
triage issue. The decisive question for a broken thing: **is the code wired but
misbehaving (`correctness`), never written (`features`), or contradicted by its docs
(`docs`)?**

- **`correctness` owns wired-but-broken behavior AND the CLAUDE.md "Fail Loudly" check**
  (`except Exception: pass`, try/except returning a placeholder, silent degradation).
- **`features`** is only genuinely-missing/half-shipped capability — a TODO for code never
  written. Wired-but-broken is correctness, not features.
- **`docs`** owns doc-vs-code drift, including a feature *documented as working but stubbed*.
- **`tests`** in deep mode rolls plain "module X has no coverage" into ONE aggregate finding
  (`dedup_key: tests:aggregate:untested-modules`); separate findings only for risk-bearing
  untested logic (auth/gate/precedence/error-mapping/#1655).

Adding a dimension means: add it to the matrix, describe its disjoint lens in the shared
prompt, and the synthesis picks up its `findings-<dim>.json` automatically.

## Published hub agents get the highest bar

Published agents are the shop window — the prompt makes every lens double-check them and
**bump any gap up one severity** (never 🟡; a default-path break is 🔴). Detect them by a
`release_agent_<id>.yml`, a shipped `SCORECARD.md`, or a released `version:` in
`gaia-agent.yaml` — currently only the **email agent**. The bar: in-sync high-quality
README/SPEC.md/SKILL.md/CHANGELOG.md (+ any contract spec) with a **real** eval `SCORECARD.md`
(gated by `gaia.eval.scorecard_gate`, never hand-authored) linked from the README; bulletproof
runtime code (no stubs/silent-fallbacks); solid #1655-grade tests. When a new agent publishes,
the detection generalizes to it automatically — no prompt edit needed.

## Severity: 🔴 high · 🟠 medium · 🟡 low — no green

Green (🟢) reads as "pass/good," so it's banned. **Broken behavior always outranks a
missing test** — never rate "module X has no tests" above a feature that's actually
broken. High = security / data loss / default-path break; medium = broken user-facing
behavior, a false doc, or a missing test guarding auth/a gate/destructive logic; low =
missing tests on non-risk logic, cosmetic gaps. The synthesis emits a section per
dimension (fixed order: Security, Correctness, Features, Docs, Tests), grouping each
finding under the dimension it **declares** — it never re-buckets.

## Child issues are 🔴/🟠 only, and tagged auto-fixable

Only high/medium findings get a child issue (and thus one-click `bug`→auto-fix promotion).
🟡 (low) findings are listed in the parent triage issue and nowhere else — this caps
tracker churn (the first deep run filed 19 children, ~13 of them low-value coverage nits).
Each finding carries an `auto_fixable` boolean; the child body says whether applying `bug`
will let auto-fix land it (locatable/small) or whether it needs a human (a test suite, a
refactor) — so maintainers don't promote something auto-fix can't handle.

## Precision gate — a false finding erodes the whole audit

Recall is cheap; trust is not. Each finding must carry `evidence` (a concrete quote or
`path:line` the dimension actually read), and the dimension prompt requires verifying the
problem is present AND not already handled elsewhere before reporting. The synthesis
**drops any 🔴/🟠 finding whose evidence doesn't substantiate its title**, and does an
**intra-run cross-dimension dedup** (a stubbed command flagged by both `docs` and
`correctness` is ONE issue, not two — keep the most severe, note the other lens).

## Dedup + suppression — the single biggest usability risk

Each finding carries `dedup_key = <dimension>:<repo-relative-path>:<symbol-or-section>`.
**The symbol is a function/class name or doc heading — NEVER a line number** (line numbers
move, so a line-based key re-files the same finding every week and the issue is unusable by
week 3). The key is embedded in each child body as `<!-- audit-key: KEY -->`. Synthesis
skips a finding whose key is in EITHER set:
- **already-filed** — keys on any *open* `weekly-audit` issue (avoid duplicates).
- **suppressed-forever** — keys on any `weekly-audit` issue also labeled **`audit-wontfix`**
  (open or closed). This is how you permanently silence accepted debt: close a child with
  `audit-wontfix` and it never comes back. Without this, wontfix findings resurface every
  deep run forever.

## Parent triage issues roll over

Each run files a NEW parent (`Weekly audit — <mode> — <run_id>`) and then **closes the
previous open parent** with a "Superseded by #N" comment — otherwise ~52 stale parents pile
up per year. Only the parent rolls over; child issues stay open (they're the actionable
units). The parent opens with a one-line tally (new/low/suppressed counts) for trend.

## Security findings stay private

A GitHub Actions run has no DM channel and the triage issue is **public**. So the
security dimension writes full detail (file, symbol, remediation) ONLY to the **job
run log** (visible to those with Actions read access), and puts a redacted stub in
`findings-security.json` — coarse directory path, no exploit, title exactly
`"Security finding (details in run log)"`. The synthesis job renders security as a
**count + run-log pointer + `@kovtcharov-amd`**, never a `file:line` or payload. This
matches the CLAUDE.md "Security Handling Protocol" and the `code-reviewer` agent's rule.
Do not "improve" this by putting detail in the public issue.

## Promotion: `bug` label → existing auto-fix job

Child issues are opened **without** any auto-fix trigger label. A maintainer promotes
one to a PR by applying the **`bug`** label; the existing `auto-fix` job in
`claude.yml` (gated on `label.name == 'bug'` **and** `contains(labels,'bug')`) then
creates the branch + PR. There is **no PR-creation code in this workflow** — humans
gate every code change. A `documentation`/`tests` label alone does NOT trigger auto-fix;
route promotions through `bug` unless you deliberately widen the auto-fix `if`.

## Cost & safety invariants

- **Model** is `AUDIT_MODEL` (top-level env, `claude-opus-4-8` — ~half the token burn of
  Fable for comparable static-review quality). One place to change it; swap to
  `claude-fable-5` for maximum depth at ~2x cost. A measured Fable deep run was ~$45 of
  API-equivalent subscription usage; Opus roughly halves that.
- **Serialized dimensions**: the matrix runs `max-parallel: 1` so the run is a steady
  drip, not a 5-job burst — this keeps it under the Max subscription's rolling (5-hour)
  rate limit. If you re-parallelize, expect a token spike that can trip that limit.
- **Skip-if-empty**: normal mode exits in `preflight` before any Claude call on a
  no-change week. Deep mode never skips.
- **Modes**: `normal` = last N days' diff; `deep` = whole codebase, auto-selected on the
  first Monday of the month (day-of-month ≤ 7, since cron only fires Mondays).
- **Read-only**: dimension + synthesis jobs run `--allowedTools Read,Grep,Glob,Bash` —
  no Edit/Write, never install or run repo code (same rule as `claude.yml` review jobs).
- **Concurrency group** `claude-weekly-audit` (not cancel-in-progress) so two scheduled
  runs never overlap and double-file.
- Auth is the same OAuth-preferred / API-key-fallback wiring as every `claude.yml` job,
  covered by `claude-auth-canary.yml`.
