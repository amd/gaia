---
name: "weekly-audit-patterns"
description: "The non-obvious invariants of the proactive nightly Claude audit workflow (.github/workflows/claude-nightly-audit.yml): the day-of-week mode selector, the two-key dedup scheme (cluster_key per defect, dedup_key per location) that keeps one defect from becoming N issues, the four audit dimensions (security has its own nightly workflow, claude-security-audit.yml) and which one owns the Fail-Loudly check, and the `bug`-label → auto-fix promotion path. Read before editing that workflow, changing its cadence, changing how findings are filed/deduped, or adding an audit dimension."
---

# Nightly Audit Patterns

`.github/workflows/claude-nightly-audit.yml` is the repo's one **proactive** Claude
lens — a scheduled deep review (not triggered by a PR) that fans out one read-only
Claude job per dimension and files **one issue per defect**. Everything else in
`claude.yml` is reactive. These are the invariants a future editor will otherwise break.

> **Naming:** this ran weekly before it moved to a nightly cron (10:37 UTC ≈ 3am Pacific,
> deep on Sundays). The filename, `name:`, cron, and concurrency group now all say
> **nightly**. Two identifiers still say `weekly` and are correct as they are:
>
> - the **`weekly-audit` label** — a **provenance** label ("a proactive Claude audit filed
>   this"), shared with the genuinely-weekly `claude-weekly-doc-walkthrough.yml`. No
>   cadence word fits both workflows, so it keeps the neutral name it happens to have;
>   its `--description` in both workflows says exactly that.
> - this **skill's `name`**, referenced from `CLAUDE.md`.
>
> An earlier version of this note justified the label by claiming a rename "would re-file
> every open finding." That is **false**, and worth correcting because it is the reasoning
> a future editor inherits: GitHub renames a label in place and every issue keeps it, and
> dedup never matched on the label text — it reads the `audit-key` / `audit-cluster`
> markers in issue bodies. The label only scopes which issues get scanned for those
> markers (`--label` in `scripts/audit/prepare_synthesis.py`, one default to update).

## The four dimensions are mutually exclusive

`correctness`, `docs`, `tests`, `features` — a matrix of one Claude job each. **Security
is NOT a dimension here** — it moved to its own workflow, `claude-security-audit.yml`
(deterministic semgrep + a Claude taint/authz/suppression sweep, CVSS-scored, findings to
the private code-scanning tab). Don't re-add it here; that created two half-owners and the
single general "security lens" is what missed the hub tar-slip.
The lenses **overlap unless the prompt keeps them disjoint**, and the first run proved it:
correctness findings (a rollback that never rolls back, a poller returning null, a mode
that no-ops) leaked into `features`, and the priciest job's output vanished from what got
filed. The decisive question for a broken thing: **is the code wired but
misbehaving (`correctness`), never written (`features`), or contradicted by its docs
(`docs`)?**

- **`correctness` owns wired-but-broken behavior AND the CLAUDE.md "Fail Loudly" check**
  (`except Exception: pass`, try/except returning a placeholder, silent degradation).
- **`features`** is only genuinely-missing/half-shipped capability — a TODO for code never
  written. Wired-but-broken is correctness, not features.
- **`docs`** owns doc-vs-code drift, including a feature *documented as working but stubbed*.
- **`tests`** in deep mode gives every plain "module X has no coverage" the shared
  `cluster_key` `tests:aggregate-untested-modules` so they merge into one issue; separate
  findings only for risk-bearing untested logic (auth/gate/precedence/error-mapping/#1655).

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

## Only 🔴/🟠 get an issue — 🟡 lives in the job summary

Only high/medium defects are filed (and thus get one-click `bug`→auto-fix promotion). 🟡
(low) findings appear in the run's job-summary report and nowhere else — this caps tracker
churn; the first deep run filed 19 issues, ~13 of them low-value coverage nits. Each
finding carries an `auto_fixable` boolean, and the issue body says whether applying `bug`
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
  filing, and demotes over-stated findings rather than deleting them. The *mechanical*
  half of its old job — merging one defect's locations, including across dimensions —
  now happens before it, in the deterministic dedup pass below.

If the tracker gets noisy, tighten synthesis. Do not re-muzzle the lenses.

## Findings are written for a human, not an auditor

Every filed issue follows [CLAUDE.md → How You
Communicate](../../../CLAUDE.md#how-you-communicate): the title and opening line say what
broke and who it hurts, in plain words; the `path:line` evidence goes underneath in a
sub-bullet or a `<details>` block. A finding that opens with a symbol name is one a
triager skips.

## ONE ISSUE PER DEFECT — the invariant everything else serves

A defect is what a maintainer **fixes**, not where they see it. One root cause spanning 39
files is one fix, so it gets ONE issue listing 39 locations. Both halves of this have
already broken, in a single night each: file-scoped keys turned one dead
`lemonade-server serve` command into five issues, and dedup that searched only the
`weekly-audit` label could not see the four-month-old #1077, so the audit re-discovered it
14 times.

Every finding carries **two** keys, and they are not interchangeable:

- **`cluster_key` = `<dimension>:<root-cause-slug>`** identifies the DEFECT and contains
  **no path**. Every location of one defect carries the *identical* key and merges into
  one issue. The test the lens prompt gives: *how many separate PRs would fix all of
  this?* — that is how many cluster keys there should be.
- **`dedup_key` = `<dimension>:<path>:<symbol-or-section>`** identifies the LOCATION. The
  symbol is a function/class name or doc heading, **NEVER a line number** (line numbers
  move, so a line-based key re-files the same finding every run).

Filed issues embed both as `<!-- audit-cluster: KEY -->` and `<!-- audit-key: KEY -->`, and
the dedup pass reads *either* marker — that back-compatibility is what stopped the
two-key change from re-filing the ~130 findings already open.

**`scripts/audit/prepare_synthesis.py` is the dedup pass**, run by the `synthesize` job of
both this workflow and the doc walkthrough, *before* the Claude filing step. It merges
findings by `cluster_key`, searches the **whole open backlog** (not one label) for an issue
already tracking each defect, flags likely sibling clusters within a run, and writes
`synthesis-dossier.md` as the model's worklist. It is deterministic on purpose: asking a
model to eyeball dedup across ~900 open issues produced a 2.35x duplication ratio. Do not
move this back into the prompt, and do not narrow the search back to one label.

Synthesis then picks exactly one of three outcomes per defect: **drop** it (the evidence
gate), **comment** on the existing backlog issue the dossier surfaced, or **file one** new
issue. Commenting is the #1077 fix — err toward it, because a comment on the wrong issue
is trivially undone and a duplicate issue is what created this backlog.

**Suppression is load-bearing, and it is now SCOPED to the key that carries it.** Closing
an issue with **`audit-wontfix`** (open or closed) is still the only way to permanently
silence accepted debt, but which key that issue carries decides how much it silences:

- an **`audit-cluster`** key silences the whole defect — it never comes back;
- an **`audit-key`** silences only that one location; the defect's other locations still
  get filed, minus the suppressed one.

Two matching rules follow the same split, and both exist because the naive version loses
real findings: one old per-file wontfix must not mute a defect later found in 38 more
places, and a single months-old issue must not make 38 newly-found locations vanish. So a
location-key match against an *open* issue leaves the defect `new` and hands the issue
number to synthesis as a **comment** target — which is also how the ~130 pre-clustering
one-issue-per-file findings get consolidated instead of orphaned.

## No run receipts — the per-run report is a job summary

Neither this workflow nor the doc walkthrough files a `Nightly audit — <run-id>` /
`Doc walkthrough — <run-id>` issue. Synthesis writes `triage-report.md` and a following
step appends it to `$GITHUB_STEP_SUMMARY`. There is no parent issue, no chain, no
cross-linking a prior run, and no "never close a parent" rule — that whole mechanism is
gone. It filed one bookkeeping issue per run and accumulated 19 of them, none actionable.

The report is a one-line tally (filed / commented / low / dropped) then a section per
dimension in fixed order — Correctness, Features, Docs, Tests — with each defect under the
dimension it **declares**, never re-bucketed, and 🔴 → 🟠 → 🟡 within a section. 🟡 lines
live here and nowhere else. The publishing step is plain bash under `if: always()`, so the
report survives a synthesis step that errored after writing it.

**Still enforced: the workflow never closes an issue.** Only a human does. An earlier
version auto-closed the previous parent as "superseded" and silently hid 18 unaddressed
findings the moment the next run fired (#2010). The parents are gone; the no-auto-close
rule outlives them and applies to every issue the audits file or comment on.

## Security is out of scope here — it has its own workflow

Security moved to `.github/workflows/claude-security-audit.yml` (see its own patterns
skill). This audit files **public** issues, which is the wrong channel for a
vulnerability — so the prompt now tells every lens to hand off any security issue it
notices rather than file it, and the synthesis emits **no** security section. Do not
re-add a security dimension here or route a security finding into a public issue.

## Promotion: `bug` label → existing auto-fix job

Filed issues are opened **without** any auto-fix trigger label. A maintainer promotes
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
- **A partial sweep never synthesizes.** Every lens writes `{"findings": []}` even when
  clean, so a missing file means the lens never ran: the upload fails on it, and synthesis
  hard-fails if fewer files arrive than `preflight` declared dimensions. The doc
  walkthrough gained the same gate per discovered guide (#3058) — without it a night where
  every judge crashed reached synthesis, found nothing to file, and reported a clean run.
- **Concurrency group** `claude-nightly-audit` (not cancel-in-progress) so two scheduled
  runs never overlap and double-file.
- **`dry_run` dispatch input** (both this workflow and the doc walkthrough): files and
  comments nothing, and reports what it *would* have done in the job summary. This is the
  only way to validate a dedup change against the live backlog without polluting it — a
  bad dedup pass files dozens of duplicates, which is expensive to undo and untestable any
  other way. Don't remove it.
- Auth is the same OAuth-preferred / API-key-fallback wiring as every `claude.yml` job.
  `claude-auth-canary.yml` covers the *credentials* — it does **not** cover the actor gate
  below, because the canary runs on dispatch with a human actor.
- **`allowed_bots` must name every bot that can actor a schedule event.** On `schedule`
  the actor is whoever last touched the default branch: `github-merge-queue[bot]` normally,
  `github-actions[bot]` after a release workflow commits. A bot missing from the list means
  claude-code-action rejects the run *before Claude starts* — and the run still reports
  green. That failure went unnoticed for five weeks, and then recurred when the fix was
  applied to the canary but not the six other steps (#3059). Every
  `anthropics/claude-code-action` step in a scheduled workflow now needs
  `allowed_bots: "github-merge-queue,github-actions"`;
  `tests/unit/test_claude_audit_workflow_contract.py` fails if one drops it.
