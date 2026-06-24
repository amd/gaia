# Review Rubric

Tunable policy for automated PR review on `amd/gaia`. Edit this file to retune how the
reviewer prioritizes findings, how many nits it posts, and what it skips. It is the single
source of truth for review **severity**, the **nit budget**, and **length caps**.

This rubric pairs with the review workflow (`.github/workflows/claude.yml`): the workflow
delivers this file to the reviewer from the **base branch** and enforces the non-tunable
invariants itself — the security-escalation protocol (tagging @kovtcharov-amd) and the
GAIA architecture/convention checks. Keep those OUT of this file; they are defense-in-depth
and must not depend on this rubric loading. See `CLAUDE.md` → "Issue Response Guidelines"
for the shared tone/format rules this rubric builds on.

## Correctness first

Spend the attention budget on what can actually break a user or the codebase, in this order:

1. 🔴 **Correctness & safety** — real bugs (wrong logic, crashes, data loss), security
   issues, breaking changes to public API / CLI / REST, and silent-fallback violations.
2. 🟡 **Missing tests & architecture** — new logic shipped without tests, and GAIA
   architecture/convention violations (also checked inline by the workflow).
3. 🟢 **Everything else** — style, naming, micro-optimizations, wording. This is the nit
   tier and is strictly capped (below).

A PR with one real 🔴 bug and ten style nits gets a review about the bug. Lead with
correctness; never let nit volume bury a blocking finding.

## Severity calibration

- 🔴 **Critical** — security, breaking changes, data-loss risk, or a bug that will fire in
  normal use. Always report, however many there are.
- 🟡 **Important** — a real bug in an edge path, missing tests for new logic, or an
  architecture/convention violation a maintainer would want fixed before merge.
- 🟢 **Minor / nit** — style, formatting, naming, optional improvements. Subject to the cap.

When unsure whether something is 🟡 or 🟢, ask "would this break or mislead a user?" If no,
it is a nit.

## Nit cap

Post **at most 5 nits** total across the whole review. If you find more, post the five
highest-value ones, then summarize the rest as a single pattern ("several files use
`print()` instead of the logger — worth a sweep") and stop. Do not enumerate every
occurrence, and never let nits outnumber substantive findings.

## Skip these (do not flag, do not list as strengths)

CI already enforces these, or the project has decided against them — flagging them is noise:

- Anything `python util/lint.py --all` fixes: **black** formatting, **isort** import order,
  trailing whitespace, missing EOF newlines, line length.
- Copyright headers, SPDX identifiers, and license boilerplate — this is an open-source
  project and contributors retain their own copyright.
- Generated, vendored, or build-output files.
- Pure type-hint-completeness nags (type checking is advisory here, not a merge gate).

If a formatting problem is so pervasive it signals a missing pre-commit hook, say that ONCE
as a process note instead of flagging individual lines.

## Still worth a suggestion

Correctness-first does not mean silent on small wins. A `suggestion` block is welcome for a
genuinely useful, low-risk fix — a clear typo in a docstring, a hardcoded value that should
be a constant, a noisy log line, an obvious one-line bug fix. These count toward the nit cap
unless they fix a real bug.

## Length caps

Keep reviews skimmable:

- Visible human summary: **≤ 400 words**.
- Each blocking-issue write-up: **≤ 150 words**.
- Each nit: **≤ 50 words**.

## Lightweight re-review (per push)

The re-review that runs on each push is **stricter than this rubric, by design**: it reports
only NEW 🔴/🟡 regressions introduced since the first review and stays **completely silent on
nits**. The nit cap above is the ceiling for the first full review, not a license for the
re-review to post nits. Default to silence on a re-review.
