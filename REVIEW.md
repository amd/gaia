# Review Rubric

Tunable policy for automated PR review on `amd/gaia`. Edit this file to retune how the
reviewer prioritizes findings, how many nits it posts, and what it skips. It is the single
source of truth for review **severity**, the **nit budget**, and **length caps**.

This rubric pairs with the review workflow (`.github/workflows/claude.yml`): the workflow
delivers this file to the reviewer from the **base branch** and enforces the non-tunable
invariants itself — the security-escalation protocol (tagging @kovtcharov-amd) and the
GAIA architecture/convention checks. Keep those OUT of this file; they are defense-in-depth
and must not depend on this rubric loading. See `CLAUDE.md` → "How You Communicate"
for the shared voice this rubric builds on.

## Correctness first

Spend the attention budget on what can actually break a user or the codebase, in this order:

1. 🔴 **Correctness & safety** — real bugs (wrong logic, crashes, data loss), security
   issues, breaking changes to public API / CLI / REST, and silent-fallback violations.
2. 🟡 **Missing tests & architecture** — new logic shipped without tests, GAIA
   architecture/convention violations (also checked inline by the workflow), and a
   user-visible change shipped without **real-world evidence matched to its surface**
   (see "Real-world evidence" below).
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

## Real-world evidence

GAIA tests everything on the surface a user actually touches, and the PR must **show** that
proof. When a diff changes a user-visible surface but the **PR description** shows no matching
evidence and doesn't point to where it lives (a linked comment or evidence branch), flag it
**once** as a 🟡 (not per-file) and name the evidence that's missing. Green unit tests are not
a substitute — they gate the logic, not the surface.

- **An agent exposed in the Agent UI** (Chat, Email, …) → a live **Playwright** run against
  `gaia chat --ui` with **before→after screenshot(s)** on the PR. This is the one surface where
  text is **not** enough — API / CLI / Agent UI MCP evidence does not substitute for the screenshot.
- **MCP tools / servers** → a live MCP client call and its response (the Agent UI MCP,
  `gaia mcp serve`, for the Agent UI's own tools).
- **CLI** command/flag/output → the real `gaia <subcommand>` and its output.
- **HTTP API / REST** → the real request and the response (status + body).

Screenshot evidence should be **embedded in the PR description** as a rendered
`![](raw.githubusercontent.com/…)` image (a raw URL renders directly; an R2/assets.amd-gaia.ai
image gets camo-proxied and often won't render), not a bare link or a comment the reviewer
must scroll to find. Text evidence (CLI/API/MCP) may sit inline in the description or a
linked comment. A screenshot that doesn't render on the PR is not shown. Do **not** flag when the change touches none of these surfaces
(internal refactor, docs, tests, CI), when the author marked a surface **N/A with a reason**,
when the description points to evidence elsewhere, or when the matching evidence is already
present. This is a nudge for a missing artifact, never a demand to re-run what the PR shows.

**CI-lane evidence legitimately defers the pixels.** The automated evidence stage runs on a
no-inference runner, so for a UI-backed change it exercises the underlying HTTP route / CLI
layer and marks only the rendered screenshot *"pending strix-halo lane"*. Treat present
route/CLI evidence with a deferred screenshot as adequate for merge-time CI — nudge for the
eventual screenshot, don't block on it. The gap worth flagging is a UI-backed change with
**neither** a screenshot **nor** the underlying-route evidence this lane could have produced. A
**Spot regression** subsection (a few *adjacent* operations exercised alongside the changed
one) is expected content, not scope creep.

## Sibling paths

The most common bug that gets past review is a change that lands on one path while a sibling
stays broken. On a PR of 200+ changed lines (`grep -c '^[+-]' pr-diff.txt`), before you write
the verdict, take each behaviour the diff changes and search the code outside the diff for:

- other callers of what changed, and other implementations of the same rule
- hard-coded lists that must gain or lose an entry (tools, models, event types, routes)
- the same flow reached another way: the Agent UI, the TUI, the CLI, a relayed or background
  run, and the streaming and non-streaming branches

A sibling still on the old behaviour is a 🟡, or a 🔴 when users reach it in normal use. Name
the sibling and how you found it. If the PR has no behaviour change, skip this.

## Known bug classes

`/tmp/missed-bug-classes.md` lists the bugs that keep shipping. Apply every **Code:** line to
the changed code, whatever the PR's size, and trace startup, restart and lock changes from an
empty `~/.gaia` and from a server that just restarted. Skip the **Docs**, **Walkthrough** and
"Don't report" parts: those are for the audits.

## Documentation drift

Run this on every PR. Most doc bugs are made here: a PR corrects the docs it remembers and
misses the others that repeat the claim.

When the diff changes behaviour a reader relies on (a default, limit, flag, command, status
code, error message, field, name or UI label), take the **old** value or wording from the
removed lines and grep for it across `docs/`, `hub/agents/**/*.md`, `hub/skills/**/SKILL.md`,
any shipped contract (`spec_html.py`, `specification.html`, `openapi.*.json`), the root
`README.md`, and `cpp/`. Search the old claim, not the file the PR already touched, and check
hand-maintained tables for a row the change added or removed.

- A doc that still states the old behaviour is a 🟡, or a 🔴 when a command it gives now fails.
- Report each stale claim **once**, listing every place, and name a doc the PR should have
  changed. These do not count toward the nit cap.
- A PR that edits docs: check its new claims against the code, too.
- Skip this for changes nothing documents: internal refactors, tests, CI. Skip a claim that
  only appears in a past CHANGELOG entry or release note, which record what was true then.

On a re-review, check only behaviour the new commits changed.

## The verdict is enforced

Part 1 opens with exactly one of `**Verdict: Approve**`, `**Verdict: Approve with suggestions**`
or `**Verdict: Request changes**`, on its own line. The `PR review verdict` status reads that
line, and a Request changes stays failing until a push passes re-review or a maintainer adds
the `review-override` label. A reply does not clear it. So use Request changes only for a 🔴 or
🟡 that must be fixed before merge.

## Length caps

Keep reviews skimmable:

- Visible human summary: **≤ 400 words**.
- Each blocking-issue write-up: **≤ 150 words**.
- Each nit: **≤ 50 words**.

## Lightweight re-review (per push)

The re-review that runs on each push is **stricter than this rubric, by design**: it reports
only NEW 🔴/🟡 regressions introduced since the first review, plus any blocking finding from
an earlier Request changes that is still not fixed, and stays **completely silent on nits**. The nit cap above is the ceiling for the first full review, not a license for the
re-review to post nits. Default to silence on a re-review.
