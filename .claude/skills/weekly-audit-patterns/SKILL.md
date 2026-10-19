---
name: "weekly-audit-patterns"
description: "The non-obvious invariants of GAIA's proactive Claude audits: the one-audit-a-night rotation (claude-audit-rotation.yml) over the code audit (claude-nightly-audit.yml), security audit and doc walkthrough; the quota gate and since-last-success carry-over; the two-key dedup scheme (cluster_key per defect, dedup_key per location) and closed-duplicate redirect that keep one defect from becoming N issues; the four code-audit dimensions and which owns the Fail-Loudly check; the missed-bug-classes doc the prompts read; and the `bug`-label → auto-fix promotion path. Read before editing any of those workflows, changing cadence, changing how findings are filed/deduped, or adding an audit or a dimension."
---

# Proactive Audit Patterns

Three **proactive** Claude audits review the repo on a schedule (not on a PR): the code
audit `.github/workflows/claude-nightly-audit.yml` (display name "Claude Code Audit"),
`claude-security-audit.yml`, and `claude-weekly-doc-walkthrough.yml` ("Claude Doc
Walkthrough"). The code audit fans out one read-only Claude job per dimension and files
**one issue per defect**. Everything in `claude.yml` is reactive. These are the
invariants a future editor will otherwise break.

> **Naming:** filenames keep their history (`nightly`, `weekly`) because docs, skills,
> tests and the dispatcher reference them; the display names are cadence-neutral. Two
> more identifiers say `weekly` and are correct as they are:
>
> - the **`weekly-audit` label** — a **provenance** label ("a proactive Claude audit filed
>   this"), shared by the code audit and the doc walkthrough. It names no cadence.
> - this **skill's `name`**, referenced from `CLAUDE.md`.
>
> `claude-account-switch.yml` watches the audits by **display name** (`workflow_run`), so
> renaming one without updating its list silently unhooks the account switch; a contract
> test checks it.

## One audit a night — the rotation is the only scheduler

`claude-audit-rotation.yml` fires once at 10:13 UTC (03:13 PDT / 02:13 PST; only
10:00–11:00 UTC is inside 2–4am Pacific in both seasons) and dispatches **one** audit:
`days since 1970-01-01 mod len(ROTATION)` in `scripts/audit/audit_rotation.py`. Each audit
therefore runs every third night. Keyed on the epoch day so it never resets at a month or
year end. **Adding an audit = append it to `ROTATION`** and give it a `rotation_night`
input plus the `run-name` the others use. None of the audits may carry its own `schedule:`
(a contract test fails if one does): three audits every night spent the subscription's
**weekly** cap mid-week (both daily audits failed 5 of 10 nights in October 2026).

**Carry-over, not retry.** A failed night is not re-run the next night (that belongs to
another audit). Instead every audit scopes itself from its own last **successful
rotation run** (`scripts/audit/audit_history.py`): the code audit reviews everything since
then, the security audit's skip-if-empty counts commits since then, and the walkthrough
walks the guides that waited longest. Only rotation runs (run-name "… rotation night N")
and legacy cron runs count as history; a hand dispatch may be a dry run that filed
nothing.

**Quota gate.** Each audit's first job probes Claude once (`scripts/audit/require_claude.py`,
same classifier as the canary and account switch). Refused → the run fails there with the
account and reset time in the summary, before any lens or self-hosted job starts. On a
quota verdict the `request-account-switch` job **dispatches** `claude-account-switch.yml`.
Don't drop that for the switch's `workflow_run` hook: the rotation starts every audit with
`GITHUB_TOKEN`, GitHub only promises that `workflow_dispatch`/`repository_dispatch` from that
token create runs, and it does not document `workflow_run` firing for such a run's completion.
A contract test checks every audit has the dispatch job.

**Explicit modes are honoured.** Only `auto` (what the rotation sends) may escalate to a deep
sweep. A hand-dispatched `mode=normal` with no recent successful run reviews the last 7 days
and says in the summary what that leaves out.

**Recent changes, read first.** The same first job writes `recent-changes.md`
(`scripts/audit/recent_changes.py`): first-parent commits since the commit the last
successful run checked out (exact; the run history is the state store), with PR titles,
body excerpts and paths, capped at 30 KB. Without that commit it covers 3 days and says so.
Lenses look there first; synthesis drops anything a listed PR already fixed.

**Missed bug classes.** All three prompts read `scripts/audit/missed_bug_classes.md`:
per-lens checklists for the classes fix PRs show the audits missing (no deadline, shared
state across processes, dishonest status, cold start, Windows, model output, release
ordering, doc drift, security) and a "Don't report" list from what got closed as noise.
`python scripts/audit/miss_analysis.py` refreshes its evidence table; edit the checklists
when a class climbs. Run it monthly.
>
> An earlier version of this note justified the label by claiming a rename "would re-file
> every open finding." That is **false**, and worth correcting because it is the reasoning
> a future editor inherits: GitHub renames a label in place and every issue keeps it, and
> dedup never matched on the label text — it reads the `audit-key` / `audit-cluster`
> markers in issue bodies. The label only scopes which issues get scanned for those
> markers (`--label` in `scripts/audit/prepare_synthesis.py`, one default to update).

## The four code-audit dimensions are mutually exclusive

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
`gaia-agent.yaml` — currently the **email agent** and the **gaia flagship agent**. The bar: in-sync high-quality
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

Two more dedup rules, both from the October 2026 cleanup (34 open duplicates closed):

- **A closed duplicate keeps pointing at its canonical.** Issues closed as duplicate hand
  their markers to open issue M (`redirect_duplicates`). The canonical comes from GitHub's
  own record first (the `MarkedAsDuplicateEvent` the UI's "Close as duplicate" writes, which
  leaves no comment), then a "Duplicate of #M" comment or body line. Otherwise consolidating
  into an umbrella like #1077 makes the next run re-file every duplicate it just closed. The
  closed-issue scan (`scripts/audit/closed_duplicates.py`) pages through small GraphQL
  queries and fails loudly at its hard cap rather than silently reading part of the list.
- **Candidate matching reads an audit issue's own keys, not just its title.** Titles alone
  let same-location duplicates through (#3832 scored 0.15 against #3471, same guide and
  section); with the markers both rank first.
- Both syntheses share the `claude-audit-issue-filing` concurrency group, so two runs never
  read the backlog at the same time and file the same defect twice.

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

- **Model** is `AUDIT_MODEL` (top-level env, `claude-opus-5-5` — $4/$20 per MTok, under half
  Fable's price for comparable static-review quality, run at its default `medium` effort). One place to change it; swap to `claude-fable-5` for maximum depth at ~2x
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
- **Skip-if-empty**: a normal-mode turn exits in `preflight` before any Claude call when
  nothing merged since the last successful run. Deep mode never skips.
- **Cadence**: every third night via `claude-audit-rotation.yml` (above). Measured October
  2026, API-equivalent: code audit ~$10 a normal turn / ~$30 deep, security ~$10, walkthrough
  ~$7 per guide (10 guides a turn).
- **Modes**: `normal` = everything merged since the last successful rotation run; `deep` =
  whole codebase, whenever the last successful deep sweep is **over 8 days old** (every
  third turn, and the very next turn after a deep sweep fails), or when there is no usable
  history or the gap exceeds 7 days. The lens job is named `audit (<dim>, <mode>)` so the
  history can tell a past deep sweep from a normal one.
  ⚠️ Never key deep mode off a weekday or day-of-month: at a 3-night cadence "Sunday" lands
  on this audit's night once in 21 days, and a day-of-month test once ran seven consecutive
  deep sweeps.
- **Read-only**: dimension + synthesis jobs run `--allowedTools Read,Grep,Glob,Bash` —
  no Edit/Write, never install or run repo code (same rule as `claude.yml` review jobs).
- **A partial code/security sweep never synthesizes.** Every lens writes
  `{"findings": []}` even when clean, so a missing file means the lens never ran: the upload
  fails on it, and synthesis hard-fails if fewer files arrive than `preflight` declared
  dimensions.
- **The walkthrough is per guide, not all-or-nothing.** Zero guides reporting is a hard
  failure (#3058: a night where every judge crashed once reported a clean run). A partial
  night files what the guides that finished found, and names each missing guide as an
  `::error`; their failed matrix jobs keep the run red. Refusing the whole night instead let
  one always-failing guide (`code-index.mdx` failed four turns running in Sep–Oct 2026) stop
  every other guide from being recorded, so the rotation was stuck on the same ten.
  `audit_history.py rotate` reads each guide's **own** job: walked = its job passed and that
  run's `synthesize` job passed. After 3 failed turns in a row a guide is **backed off**
  (one retry every 9 days, an `::error` and a table in the summary) so it can't hold a slot
  every turn. Only turns where another guide finished count: a night where every guide
  failed (usage limit, a runner with no Git) is the night's fault, and counting it backed
  off 11 of 29 guides on the real Sep–Oct history. A guide is "changed" when its file differs from the commit of **its own** last
  walk (never a run-wide base that only moves on a green run), and changed guides take at
  most half the slots.
- **Concurrency group** `claude-nightly-audit` (not cancel-in-progress) so two runs never
  overlap and double-file.
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
