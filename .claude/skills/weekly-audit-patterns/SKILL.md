---
name: "weekly-audit-patterns"
description: "The non-obvious invariants of the proactive weekly Claude audit workflow (.github/workflows/claude-weekly-audit.yml): the stable dedup-key scheme that keeps it from re-filing findings every week, the private channel for security findings, the five audit dimensions and which one owns the Fail-Loudly check, and the `bug`-label → auto-fix promotion path. Read before editing that workflow, changing how findings are filed/deduped, or adding an audit dimension."
---

# Weekly Audit Patterns

`.github/workflows/claude-weekly-audit.yml` is the repo's one **proactive** Claude
lens — a scheduled deep review (not triggered by a PR) that fans out one read-only
Claude job per dimension and files a ranked triage issue. Everything else in
`claude.yml` is reactive. These are the invariants a future editor will otherwise break.

## The five dimensions (and the one that owns Fail-Loudly)

`security`, `correctness`, `docs`, `tests`, `features` — a matrix of one Claude job
each. **`correctness` owns the CLAUDE.md "No Silent Fallbacks — Fail Loudly" check**
(`except Exception: pass`, try/except that returns a placeholder, silent degradation).
It is not a separate dimension and it is not homeless — if you add a `code-quality`
dimension, move that check explicitly, don't leave it implied. Adding a dimension
means: add it to the matrix, describe its lens in the shared dimension prompt, and the
synthesis job picks up its `findings-<dim>.json` automatically.

## Dedup key — the single biggest usability risk

Each finding carries `dedup_key = <dimension>:<repo-relative-path>:<symbol-or-section>`.
**The symbol is a function/class name or doc heading — NEVER a line number.** Line
numbers move every time the file changes, so a line-based key re-files the same finding
every week and the triage issue is unusable by week 3. The key is embedded in each
child issue body as `<!-- audit-key: KEY -->`; the synthesis job lists open
`weekly-audit` issues, parses those markers, and **skips any finding whose key already
has an open child**. Closing a child (fixed or wontfix) lets it resurface only if still
present. Line numbers may appear in the human-readable title/why, never in the key.

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
