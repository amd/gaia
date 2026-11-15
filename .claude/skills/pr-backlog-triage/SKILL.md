---
name: pr-backlog-triage
description: "Interactively clear a backlog of open amd/gaia PRs to zero (or to a short, justified human-review list) by fanning out one isolated subagent per PR. Use when the user asks to 'audit open PRs', 'triage the backlog', 'fix CI and merge what's safe', or similar — not for reviewing a single named PR someone just opened (that's a normal review), and not for the scheduled nightly audit workflow (see weekly-audit-patterns, a different, automated lens)."
---

# PR Backlog Triage

Clears a large, standing backlog of open PRs by dispatching one worktree-isolated
subagent per PR, in parallel, repeatedly, until nothing mergeable is left open. This
is a *loop*, not a one-shot pass: new PRs land while you're triaging the old ones,
PRs you held come back with fixes, and CI itself sometimes breaks underneath the
whole batch. Budget for several rounds in one sitting.

## Before the first round: sync to `origin/main`

A worktree's local `main` can silently fall behind `origin/main` by dozens of commits
— nothing here force-pushes or rebases your worktree, so staleness only clears when
you fetch. Running tests or reproducing a bug against a stale worktree produces a
false report: a bug "found" that a merged PR already fixed, or a fix that looks
broken because the test it needs hasn't landed yet. Check and fast-forward before
trusting any local result:

```bash
git fetch origin main
git log --oneline HEAD..origin/main | wc -l   # non-zero means you're behind
git merge --ff-only origin/main               # safe: fails loudly instead of rewriting history if it can't fast-forward
```

Re-run this at the start of every round, not just once — rounds in this loop can span
hours, and `main` moves fast when several PRs merge per round.

## Order of work

1. **Third-party low-risk bug fixes first**, each with a full line-by-line security review.
2. **Maintainer and bot bug fixes.**
3. **Ready features.**

Milestones: a bug goes to the patch milestone; a feature that isn't ready goes to the next
minor. Mark a release blocker only for a genuine ship-stopper.

A PR that wires in an outside company's product gets the `third-party integration` label and
is vetted per `../../../REVIEW.md` → "Third-party integrations".

## Fast lane for small fixes

Batch small fixes about 12 to a reviewer-merger agent, grouped by area, duplicate pairs
first. That agent:

- merges only clean PRs. It wrote nothing, so it is the independent verifier.
- closes the weaker PR of a duplicate pair.
- pushes no fixes. Anything not ready goes in a NOT-READY report, naming the exact blocker
  and the fix needed, for the maintainer. The one exception is a mechanical keep-both
  conflict in tests, locale files, or a CHANGELOG.

## Verification

- **Never self-merge.** An agent that authored a PR's final commits must not merge it; a
  separate verifier does. A fix-and-merge shortcut once let 4 regressions land, and only an
  independent post-merge review caught them.
- **Cap fix-then-verify loops at about 3 rounds**, then hold for the maintainer with the
  exact trade-off. When each patch trades one bug for another, change approach (e.g. a
  linear tokenizer plus a differential corpus test against `main`, not another regex).
- **Heuristic and answer-check changes:** the verifier runs branch and `main` over a corpus.
  Any new false positive blocks the merge.
- **Security fixes:** a new false refusal of common usage blocks the merge, the same as a
  bypass.

## Infra realities

- **The repo's `GITHUB_TOKEN` API budget runs out under heavy merge volume.** Rate-limit reds
  on label, verdict, CodeQL, or "Select unit tests" jobs don't block the merge, but they mean
  the tests never ran. Re-run them; never merge past a test job that didn't run.
- **`main` has no required status checks**, so the merge queue doesn't gate. Wait for the
  PR's own CI yourself.
- **Cap local pytest at `-n 4`** when several agents share one PC.
- **Approving a fork's CI run:** read the head diff first. If it touches `.github/` or an
  installer, flag it to the maintainer instead of approving.

## Per-PR subagent dispatch

One `Agent` call per PR, `isolation: "worktree"`, run in parallel (multiple
`Agent` invocations in a single message). Each subagent's prompt should carry:

1. **The PR number, title, and author** — enough that the agent doesn't have to
   guess what it's for from the diff alone.
2. **The sync-to-main step above**, inline — a fresh worktree needs it too.
3. **Known non-blocking CI causes, as categories, not hardcoded issue numbers.**
   Issue numbers for flakes get fixed and re-opened as the codebase moves; a skill
   that hardcodes "failure X is always issue #1234, ignore it" goes stale and
   becomes wrong in the opposite direction — treating a *new* regression as a known
   flake because it superficially resembles one. Instead, tell the agent the
   *shape* of a known flake and to verify by reading the actual job log every time:
   - A `continue-on-error: true` smoke-test job failing on a platform-specific
     assertion unrelated to the PR's own files (e.g. a POSIX-only check running on
     Windows, a macOS-only library conflict) — real flakes recur verbatim across
     unrelated PRs; confirm by reading the log, not by the job's name alone.
   - The automated PR-review bot skipping with a quota/auth error rather than
     posting a verdict — treat as "never ran," not a finding either way.
   - A failure that reproduces identically on bare `origin/main` with none of the
     PR's own changes present — proves the PR isn't the cause; still worth filing
     an issue if nothing already tracks it (see "What a subagent should still do"
     below).
4. **Fork vs. same-repo handling.** Check `headRepositoryOwner`/`headRepository`
   before assuming push access. Same-repo branches: push fixes directly. Fork
   branches: push to the fork remote (`git push <fork-owner>-fork HEAD:branch`),
   never to `amd/gaia` directly, and never force. A push that ends up on the wrong
   remote (easy to do when a worktree still has `origin` pointed at the main repo)
   needs to be deleted and redone, not left stray.
5. **The eval-gating rule.** If the diff touches a system prompt, tool docstring,
   tool-call schema, tool-registration/availability logic, retry/recovery prompts,
   or verification/grounding logic, it's LLM-behavior-affecting and needs a real
   `gaia eval agent` (or `gaia eval tasks`) run with posted results before merge —
   per CLAUDE.md. A PR with a clean diff and green CI but no eval evidence for this
   category of change is a **hold, not a merge**. Draft-status language left in the
   PR body ("needs the eval run", "draft until...") after the PR left draft is a
   strong signal the author knows this gate is unmet — don't treat coming out of
   draft as the gate having cleared.
6. **What to actually do**, in order: read the diff, description, and every
   comment (not just the latest) — a bot review's finding from hours ago is still
   unaddressed if no commit followed it. Check fork status. Fix merge conflicts
   (additively — when two features touched the same lines, keep both, don't drop
   either side without understanding why it was there). Root-cause every failing
   check from the actual log, not the job name. Run the affected test suite with
   `PYTHONPATH` pointed at *this* worktree's `src/` + `hub/agents/*/python/` (an
   editable install elsewhere on the machine will otherwise silently test the
   wrong checkout — see `project_editable_install_wrong_worktree` in memory, or the
   repo's own root `conftest.py` pin for pytest specifically). Run lint. If you pushed
   any commit, stop there and hand the PR to a separate verifier (see "Verification");
   otherwise merge via `gh pr merge <n> --squash`, falling back to `--auto` when the
   repo's merge queue rejects a direct squash (`"! The merge strategy for main is set by the merge
   queue"` is the queue accepting it, not refusing it — re-check `state`/`mergedAt`
   afterward since a queued merge can still fail later and the PR stays open).
7. **When to hold instead of merge:** a real bug whose fix is still incomplete: a
   confirmation prompt that can approve a declined action's full scope without
   showing it, a cache keyed wrong, a leak path only half-closed, a race "narrowed"
   rather than closed, a doc deletion that hides a still-live, still-supported
   surface rather than reflecting an actual retirement. State the gap precisely —
   "what's missing before this can merge" — not just "needs more review."
8. **Large PRs (roughly >5k lines or a full subsystem rewrite) get resolved to a
   clean, CI-green, conflict-free state but are never auto-merged**, regardless of
   how clean they are — they get flagged for explicit human sign-off. Still do the
   full conflict-resolution and bug-hunting work; don't skip triage just because
   it's big.
9. **Security-sensitive PRs** (permission prompts, confirmation gating, path-scope
   enforcement, anything in the "ask before doing X" family) get the same
   fail-closed scrutiny every time: trace what happens on timeout, on a malformed
   answer, on an answer to a *different* prompt arriving late, and whether a grant
   can be scoped wider than what was actually shown to the user. These also get
   held for human sign-off even when the mechanism checks out — the two concerns
   (size/security and "is it merge-ready") are independent; a PR can clear one and
   still need the other.

## What a subagent should still do even when holding

Don't just report a finding and stop. Post it as a PR comment if nothing already
says it (a bot review that already caught the same thing doesn't need a duplicate).
If the finding is a real bug unrelated to the PR under review — a CI failure that
reproduces on bare `main`, a stale pinned test, a leftover reference to something
already deleted — fix it in its own small PR (or file an issue if it's bigger than
a one-commit fix) rather than letting it silently block every other PR in the
batch. Check whether another parallel subagent already found and fixed the same
thing before filing a duplicate — two subagents hitting the same shared-file break
independently is common in a big parallel batch.

## Re-verifying a previously-held PR

When a round turns up a new commit on a PR you held last round, don't assume the
new commit fixes what you flagged — re-read the actual diff and re-verify the
specific claim. A plausible-looking follow-up commit sometimes addresses a
*different*, superficially similar concern (e.g. adds an unrelated optimization
instead of fixing the flagged correctness gap) and the original hold stands.

## End state

The loop is done for a round when every open PR is either merged, or held with a
specific, current, re-checked reason (not "needs review" — the *actual* blocking
fact). Summarize the held list for the user with one line per PR naming the
concrete reason, grouped by reason category (eval-gate unmet / incomplete fix /
large-PR sign-off / security sign-off), so a human can scan it in seconds rather
than re-deriving why each one is still open.
