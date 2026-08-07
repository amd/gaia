---
name: "weekly-audit-patterns"
description: "The non-obvious invariants of the proactive nightly Claude audit workflow (.github/workflows/claude-weekly-audit.yml): the day-of-week mode selector, the stable dedup-key scheme that keeps it from re-filing findings every run, the four audit dimensions (security has its own nightly workflow, claude-security-audit.yml) and which one owns the Fail-Loudly check, and the `bug`-label → auto-fix promotion path. Read before editing that workflow, changing its cadence, changing how findings are filed/deduped, or adding an audit dimension."
---

# Nightly Audit Patterns

`.github/workflows/claude-weekly-audit.yml` is the repo's one **proactive** Claude
lens — a scheduled deep review (not triggered by a PR) that fans out one read-only
Claude job per dimension and files a ranked triage issue. Everything else in
`claude.yml` is reactive. These are the invariants a future editor will otherwise break.

> **Naming:** this ran weekly until it moved to a nightly cron (10:37 UTC ≈ 3am Pacific,
> deep on Sundays). Three identifiers deliberately kept the `weekly` name so the change
> did not orphan existing state or break references: the **filename**, the
> **`weekly-audit` issue label** (it is the dedup key — renaming it would re-file every
> open finding), and this **skill's `name`** (referenced from `CLAUDE.md`). Issue titles
> and the workflow display name did change, to `Nightly audit — …`.

## The four dimensions are mutually exclusive

`correctness`, `docs`, `tests`, `features` — a matrix of one Claude job each. **Security
is NOT a dimension here** — it moved to its own workflow, `claude-security-audit.yml`
(deterministic semgrep + a Claude taint/authz/suppression sweep, CVSS-scored, findings to
the private code-scanning tab). Don't re-add it here; that created two half-owners and the
single general "security lens" is what missed the hub tar-slip.
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
dimension (fixed order: Correctness, Features, Docs, Tests), grouping each
finding under the dimension it **declares** — it never re-buckets.

## Child issues are 🔴/🟠 only, and tagged auto-fixable

Only high/medium findings get a child issue (and thus one-click `bug`→auto-fix promotion).
🟡 (low) findings are listed in the parent triage issue and nowhere else — this caps
tracker churn (the first deep run filed 19 children, ~13 of them low-value coverage nits).
Each finding carries an `auto_fixable` boolean; the child body says whether applying `bug`
will let auto-fix land it (locatable/small) or whether it needs a human (a test suite, a
refactor) — so maintainers don't promote something auto-fix can't handle.

## Findings are filtered in SYNTHESIS, never in the lenses

**The lenses report everything they can ground in something they read — including
findings they are unsure about. Synthesis is the only filter.** This split is deliberate
and load-bearing on Claude Opus 5: a lens prompt that says "be conservative", "precision
beats recall", or "skip anything you aren't confident is real" gets followed *literally*
— the model investigates just as hard and then silently reports less. Both this workflow
and `claude-security-audit.yml` previously said exactly that. Do not put it back. (The
security audit's own origin story is a recall failure: its predecessor missed the hub
tar-slip, CWE-22, by sampling and trusting a suppression comment.)

What each side owns:
- **Lens**: grounding only. Open the cited file, confirm the problem is present, confirm
  it isn't already handled, and fill `evidence` with what you actually read. **No
  evidence, no finding** — the one hard filter upstream. Express doubt through
  `severity` (file it 🟡 and say so in `why`), never by dropping the item.
- **Synthesis**: the real gate. Drops any finding at any severity whose evidence doesn't
  substantiate its title, re-reads the cited `path`/`symbol` for every 🔴/🟠 before
  filing, demotes over-stated findings rather than deleting them, collapses an
  implausible flood on one area into a single finding, and does an **intra-run
  cross-dimension dedup** (a stubbed command flagged by both `docs` and `correctness` is
  ONE issue, not two — keep the most severe, note the other lens).

If the tracker gets noisy, tighten synthesis. Do not re-muzzle the lenses.

## Dedup + suppression — the single biggest usability risk

Each finding carries `dedup_key = <dimension>:<repo-relative-path>:<symbol-or-section>`.
**The symbol is a function/class name or doc heading — NEVER a line number** (line numbers
move, so a line-based key re-files the same finding every run and the issue is unusable by
week 3). The key is embedded in each child body as `<!-- audit-key: KEY -->`. Synthesis
skips a finding whose key is in EITHER set:
- **already-filed** — keys on any *open* `weekly-audit` issue (avoid duplicates).
- **suppressed-forever** — keys on any `weekly-audit` issue also labeled **`audit-wontfix`**
  (open or closed). This is how you permanently silence accepted debt: close a child with
  `audit-wontfix` and it never comes back. Without this, wontfix findings resurface every
  deep run forever.

## Parent triage issues accumulate — the workflow NEVER closes one

Each run files a NEW parent (`Nightly audit — <mode> — <run_id>`; synthesis also matches the legacy `Weekly audit — ` prefix so the chain survives the rename) and **cross-links the
most recently created open parent** with a comment ("Follow-up audit run filed as #N —
stays OPEN until its findings are addressed") — it does **not** close it. An earlier
version auto-closed the previous parent as "superseded," which silently hid an epic's
still-open child findings the moment the next run fired (#2010: 18 unaddressed children
went dark). Only a **human maintainer** may close a parent, and only after its findings
are addressed. Child issues stay open regardless (they're the actionable units) — that
was already true and is unchanged.

Since parents are never auto-closed, there can be several open `Nightly audit —` (and legacy `Weekly audit —`) issues at
once; synthesis always cross-links the highest-numbered (most recent) one, never assumes
there's "at most one." The tradeoff is deliberate: parents pile up until a maintainer
closes them, trading tracker tidiness for guaranteeing unaddressed findings stay visible.
The parent opens with a one-line tally (new/low/suppressed counts) for trend. On a
zero-new-findings run, synthesis posts nothing and does not touch any prior parent.

## Security is out of scope here — it has its own workflow

Security moved to `.github/workflows/claude-security-audit.yml` (see its own patterns
skill). This audit files to a **public** triage issue, which is the wrong channel for a
vulnerability — so the prompt now tells every lens to hand off any security issue it
notices rather than file it, and the synthesis emits **no** security section. Do not
re-add a security dimension here or route a security finding into the public issue.

## Promotion: `bug` label → existing auto-fix job

Child issues are opened **without** any auto-fix trigger label. A maintainer promotes
one to a PR by applying the **`bug`** label; the existing `auto-fix` job in
`claude.yml` (gated on `label.name == 'bug'` **and** `contains(labels,'bug')`) then
creates the branch + PR. There is **no PR-creation code in this workflow** — humans
gate every code change. A `documentation`/`tests` label alone does NOT trigger auto-fix;
route promotions through `bug` unless you deliberately widen the auto-fix `if`.

## Cost & safety invariants

- **Model** is `AUDIT_MODEL` (top-level env, `claude-opus-5` — ~half the token burn of
  Fable for comparable static-review quality, and the same $5/$25 as the Opus 4.8 it
  replaced). One place to change it; swap to `claude-fable-5` for maximum depth at ~2x
  cost. A measured Fable deep run was ~$45 of API-equivalent subscription usage; Opus
  roughly halves that.
  ⚠️ **Model support is gated by the pinned `claude-code-action` version** — the action
  bundles the CLI that resolves model names, so a model newer than the pin is
  unresolvable and fails as a broken job rather than a clear error. Bump the SHA in the
  same change and prove it first with
  `gh workflow run claude-auth-canary.yml -f model=<new-id>`.
- **Serialized dimensions**: the matrix runs `max-parallel: 1` so the run is a steady
  drip, not a 4-job burst — this keeps it under the Max subscription's rolling (5-hour)
  rate limit. If you re-parallelize, expect a token spike that can trip that limit.
- **Skip-if-empty**: normal mode exits in `preflight` before any Claude call on a night
  with no commits. Deep mode never skips. This matters more now the cadence is nightly.
- **Cadence**: **nightly** at 10:37 UTC (≈3am Pacific). The sibling
  `claude-security-audit.yml` also runs nightly, deliberately ~1.4h earlier at 09:13 UTC
  so the two proactive Claude runs never stack on the shared subscription pool.
- **Modes**: `normal` = last N days' diff (`window_days`, default **1**); `deep` = whole
  codebase, auto-selected on **Sundays**.
  ⚠️ The selector keys off **ISO day-of-week** (`date -u +%u`, 7 = Sunday), NOT
  day-of-month. It previously read `day-of-month ≤ 7` and was only correct because the
  cron fired on Mondays alone. Under the nightly cron that test matches the 1st–7th of
  every month — **seven consecutive whole-codebase deep sweeps** instead of one. Do not
  "simplify" it back to a day-of-month check.
- **Read-only**: dimension + synthesis jobs run `--allowedTools Read,Grep,Glob,Bash` —
  no Edit/Write, never install or run repo code (same rule as `claude.yml` review jobs).
- **Concurrency group** `claude-weekly-audit` (not cancel-in-progress) so two scheduled
  runs never overlap and double-file.
- Auth is the same OAuth-preferred / API-key-fallback wiring as every `claude.yml` job,
  covered by `claude-auth-canary.yml`.
