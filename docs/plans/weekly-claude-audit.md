# Nightly Claude deep-audit → one issue per defect + human-gated PRs

Design record for `.github/workflows/claude-nightly-audit.yml`.

## Why this matters

Every Claude job in this repo is **reactive** — it fires on a PR, a comment, or an
`@claude` mention (`claude.yml`). Nothing looks at the repo *proactively*, so debt that
ships inside a green PR — a feature with no doc page, a code path with no test, a silent
`except Exception: pass`, a `cli.mdx` that drifted from the real flags — sits unnoticed
until a user hits it. This workflow adds the missing proactive lens: a scheduled deep
review that files **one issue per defect** for a maintainer to triage and promote.

**Human-gated, not an auto-merge bot.** The workflow produces *findings*, never commits.

## Modes

- **normal** (6 nights/week) — reviews the last N days of merged work (`window_days`, default 1) + the subsystems it touched. Skips entirely on a night with no commits.
- **deep** (Sundays) — whole-codebase latent-debt sweep. Auto-selected when the ISO
  day-of-week is 7; also selectable via `workflow_dispatch`. Deep runs still file issues —
  that is the point of the sweep.

> **Cadence update:** this shipped weekly and now runs **nightly** at 10:37 UTC
> (≈3am Pacific), alongside `claude-security-audit.yml` at 09:13 UTC. The mode selector
> moved from day-of-month to ISO day-of-week at the same time — under a nightly cron the
> old `day-of-month ≤ 7` test would have matched the 1st–7th of every month and fired
> seven consecutive deep sweeps. See the `weekly-audit-patterns` skill for current
> invariants.

## Dimensions (one read-only Claude job each, serialized)

> **Security is not a dimension here.** It moved to a dedicated workflow,
> `.github/workflows/claude-security-audit.yml` (deterministic semgrep + a Claude
> taint/authz/suppression sweep, CVSS-scored, findings to the private code-scanning tab).
> Filing security into this audit's **public** issues was the wrong channel, and a
> single general "security lens" is what missed the hub tar-slip.

| Dimension | Looks for |
|-----------|-----------|
| `correctness` | code that is **wired but misbehaves** — a handler that flips a success flag without doing the work (a rollback that never rolls back), a poller that always returns null, a mode that no-ops, a flag whose handler raises `NotImplementedError` — plus real logic bugs and every CLAUDE.md "Fail Loudly" violation. **Owns wired-but-broken behavior and the silent-fallback check.** |
| `docs` | new feature with no `docs/` page or `docs.json` entry; `cli.mdx` drift; `amd-gaia.ai` links missing `/docs/`; hub-agent README/SPEC/SKILL/CHANGELOG drift. A feature **documented as working but stubbed** is a docs finding (doc-vs-code drift), not features. |
| `tests` | code paths with no test, or assertions that prove invocation not call validity (#1655). In **deep** mode, every plain "module X has no coverage" gets the shared `cluster_key` `tests:aggregate-untested-modules` so they merge into one issue; separate findings only for risk-bearing untested logic (auth/gate/precedence/error-mapping/#1655). |
| `features` | a genuinely **missing or half-shipped** capability where nothing is wired yet (a TODO standing in for unwritten code). If the code is wired but broken, that is `correctness`, not features. |

The four lenses are **mutually exclusive** — the decisive question for a broken thing is
*is the code wired but misbehaving (correctness), never written (features), or contradicted
by its docs (docs)?* Without that boundary, correctness findings leak into features and the
priciest job's output disappears.

### Published hub agents — the highest bar

Published agents are the project's shop window: an integrator installs them and judges GAIA
by them. In **both** modes, each lens double-checks any published agent explicitly and
**bumps a gap up one severity** (never 🟡; a default-path break is 🔴). Published agents are
detected by a `release_agent_<id>.yml` workflow, a shipped `SCORECARD.md`, or a released
`version:` in `gaia-agent.yaml` — currently the **email agent** (`hub/agents/email/python/`
+ `hub/agents/email/npm/`). The bar, per lens:

- **docs** — README (integrator-facing, high quality), `SPEC.md` (full reference), `SKILL.md`
  (AI-integration playbook), `CHANGELOG.md`, and any shipped contract spec, all present,
  mutually consistent, and genuinely written; a `SCORECARD.md` exists and is linked from the README.
- **tests** — solid unit + integration coverage of the real request/response contract
  (#1655, not mock-only); the `SCORECARD.md` comes from a real eval and passes
  `gaia.eval.scorecard_gate` (never hand-authored).
- **correctness** — runtime code is bulletproof: no stubs, no silent fallbacks, no
  half-finished paths, actionable errors at every boundary.

(The security review of published agents runs in `claude-security-audit.yml`.)

> **Fix vs. the original handoff:** the first draft had four dimensions
> (security/tests/docs/features) and listed the silent-fallback check in the scope
> narrative but assigned it to **no** dimension — it would have fallen through. This
> design adds a **`correctness`** dimension and gives it that check explicitly, so
> the workflow covers code *correctness* (bugs) as well as code *debt* (tests, features).
> (Security was later split out into `claude-security-audit.yml`, leaving the four
> dimensions above.)

## Synthesis → one issue per defect

> **Superseded (2026-09).** Two decisions in the original design were reversed after a
> month of live runs:
> - **Parent triage issue per run — gone.** It filed one bookkeeping issue per run and 19
>   accumulated, none actionable. (An intermediate version also auto-closed the previous
>   parent as "superseded," which silently hid 18 unaddressed child findings — #2010 — so
>   closing was dropped before the parent itself was.) The per-run report is now a job
>   summary.
> - **Per-finding child issues, deduped by label — gone.** One defect spanning N locations
>   became N issues, and dedup that only scanned `weekly-audit`-labelled issues could not
>   see the four-month-old #1077, so the audit re-filed it 14 times in one night.
>
> The rest of this section describes the current design.

A synthesis job collects the four structured outputs, runs them through a deterministic
dedup pass (`scripts/audit/prepare_synthesis.py`) that merges findings by root cause and
searches the **whole** open backlog for an issue already tracking each one, and ranks by
severity (**🔴 high · 🟠 medium · 🟡 low** — no green; green reads as "pass"). For each
surviving defect it does exactly one of three things:

- **Drop it** — the evidence gate: `evidence` missing, or not substantiating the title.
  🟡 (low) defects are never filed; they appear in the run report only.
- **Comment on an existing issue** — when a backlog candidate would be closed by the same
  fix. This is the #1077 fix, and synthesis is told to err toward it.
- **File ONE issue** (🔴/🟠, labelled `weekly-audit`) listing **every** location of that
  defect. It carries each location's `evidence` and an **auto-fixable** flag; a maintainer
  promotes it with **`bug`** (→ existing `auto-fix` job) or permanently silences it with
  **`audit-wontfix`**. No new PR-creation code.

The per-run report — a one-line tally, then a section per dimension in fixed order
(Correctness, Features, Docs, Tests), each defect under the dimension it *declares*, never
re-bucketed — is written to `triage-report.md` and appended to `$GITHUB_STEP_SUMMARY`. It
is not an issue, and the workflow never closes an issue.

**Precision & lifecycle** (what makes it trustworthy over time):
- **Verify before file**: lenses must carry `evidence` (a read quote) and confirm the
  problem isn't already handled; synthesis re-reads the cited path/symbol for every 🔴/🟠.
- **One defect, one issue**: locations merge by `cluster_key`, across files and across
  dimensions, before the model sees them.
- **Permanent suppression**: a finding whose key sits on an `audit-wontfix` issue never re-files.

## Invariants (see the `weekly-audit-patterns` skill)

- **Two keys per finding.** `cluster_key` = `<dimension>:<root-cause-slug>` identifies the
  DEFECT and holds no path — every location shares it and merges into one issue.
  `dedup_key` = `<dimension>:<path>:<symbol-or-section>` identifies the LOCATION, using a
  function/class name or doc heading, **never a line number** (line numbers move and
  re-file the finding every run). A filed issue embeds both as `<!-- audit-cluster: … -->`
  and `<!-- audit-key: … -->`; the dedup pass reads either and skips any key already on an
  *open* `weekly-audit` issue OR on any `audit-wontfix` issue (accepted debt).
- **`weekly-audit` is a provenance label**, not a cadence one — it marks "a proactive
  Claude audit filed this" and is shared with the genuinely-weekly doc walkthrough.
- **Security is a separate workflow**: `claude-security-audit.yml` owns it; this audit files
  public issues, so it hands security off rather than disclosing it here.
- **Skip-if-empty**: normal mode exits before any Claude call on a night with no commits.
- **A partial sweep never files**: synthesis hard-fails when fewer dimensions reported in
  than `preflight` declared — a crash that files nothing otherwise reads as a clean audit.
- **`dry_run` dispatch input**: files and comments nothing, reports what it would have
  done. The only way to validate a dedup change against the live backlog.
- **Read-only**: `--allowedTools Read,Grep,Glob,Bash`; never install or run repo code.
- **Model** `claude-opus-5` via the top-level `AUDIT_MODEL` env (one place to change);
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
