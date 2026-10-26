---
name: contributor-triage
description: "Load whenever the user is auditing, triaging, reviewing, or completing amd/gaia pull requests or issues — 'audit/triage PRs', 'what's outstanding', 'complete the ready PRs', 'merge what's ready', 'check third-party/external/community PRs or issues', 'approve workflows for contributors', 'what's left for the release', 'label good first issues', 'close stale issues', 'are contributor fixes in main/the release notes'. Outside contributors come first, bug fixes before features. A PR merges only with CI green, no conflicts, every comment addressed and an independent verifier pass. Runs a scanner for the ranked worklist, approves stalled fork CI for reviewed commits, drives the per-PR loop, and cuts release candidates at checkpoints. Parallel subagent mechanics: pr-backlog-triage. Reply wording: github-issue-response."
---

# Contributor-first PR and issue triage

The standing job: clear what is open on `amd/gaia` into `main`, in this order, every time.

1. **Outside contributors' bug fixes** (PRs, then issues). They gave GAIA their time for free;
   a PR left without CI, a review or a reply is the most expensive item in the backlog.
2. **Maintainer bug fixes**, then dependency bumps and test/flake fixes.
3. **Everything else** (features, refactors, plans) waits behind bug fixes. During a freeze it
   is not merged; give it a milestone and move on.

"Third-party" means not a maintainer: not a push-access collaborator, not that person's
personal account, not a bot. Team-filed work (a teammate's personal fork) counts as team.

## The merge bar

A PR is **ready** only when all four hold:

- CI is green on the *current* head commit, and the required checks actually ran. A
  failure that also fails on bare `origin/main` isn't the PR's fault; say so, don't count it
  as green.
- no merge conflicts
- every comment is addressed: human comments, inline threads, and the review bot's findings
  (a stale "Request changes" is cleared by the bot re-reviewing, not by a commit alone)
- a verifier that did not touch the PR agrees

The bar is strict by default and the **user** sets any waiver ("bug-only PRs, force-complete
if comments are addressed"). A model never relaxes it on its own.

Authority: merge when the user has asked to complete PRs into `main`. Pushing a fix onto a
PR branch is part of completing it. RC and final tags follow [`gaia-release`](../gaia-release/SKILL.md),
which confirms before pushing any tag: ask unless the user has already pre-authorized RC tags,
and a final tag always needs their explicit yes. Never force-push, never `gh pr merge --admin`
(an admin merge skips the queue's re-test and has turned `main` red), and never close a
contributor's PR without a stated reason and thanks.

## Step 1 — build the worklist

```bash
python util/triage_scan.py          # table
python util/triage_scan.py --json   # for agents
```

Read-only, ~1 minute. Third-party PRs first (bug fixes, then the rest), then maintainer PRs,
then unanswered third-party issues, each PR with its blockers: conflict, mergeability still
unknown, CI stalled awaiting approval, required checks missing (`--require` overrides the
list), red or cancelled checks, a review-bot "request changes" verdict (a missing or
stale approve is only a note: the verifier reads those), unresolved review threads, comments after the last commit, draft, >5k lines. "Ready
candidate" means the cheap signals are clean: it is where reading starts, not permission to
merge. Needs `gh` authenticated with push access; on Windows set `GH` to its full path.

Before touching any PR: `git fetch origin main` and work in a fresh worktree under
`~/.cache/worktrees/gaia/pr-<N>`. Never use the user's working tree and never `git stash`.
**Check ownership first**: another session or fixer agent may own the PR (recent commits,
a sibling session's ledger). If so, ask instead of pushing on top.

Keep a ledger file outside the repo (PR, owner, head SHA, state). Connections drop and
contexts reset mid-triage; on "continue", rebuild state from `gh` plus the ledger and
verify what actually merged rather than trusting a summary.

## Step 2 — review, then approve the stalled workflows (third-party PRs)

A first-time contributor's workflows wait in `action_required` until a maintainer approves
them, so the PR looks green-ish (only label/bot checks) while its tests never ran. Approving
executes the contributor's code on our runners, so **review first, then approve, every
time — and approve right after reviewing, not later**:

1. Read the diff of the exact head commit. Look for changes to `.github/`, scripts, install
   or packaging files, dependency sources, `conftest.py`, secrets or env access, network calls
   to odd hosts, obfuscated blobs, or a payload unrelated to the title.
2. Approve, pinned to the commit you read:

   ```bash
   python util/approve_fork_runs.py --reviewed 5732@6d02e4f
   python util/approve_fork_runs.py --reviewed 5732@6d02e4f --apply
   ```

   The first command is a dry run. The tool refuses if the head moved since you read it,
   refuses sensitive paths unless you pass `--include-risky N` after reading them, skips
   maintainers' PRs, and fails loudly on any run it could not approve. A network blip leaves
   some runs unapproved; re-running the same command approves only what is still stalled.
3. A contributor push creates new stalled runs for the new SHA. Re-read the new commits,
   then approve again. Re-scan afterwards: the PR now shows "CI pending" until the runs finish.
4. A draft PR's CI is gated on the `ready_for_ci` label. Add it only after reviewing.

Approving CI is not approving the PR; Step 3 still applies.

## Step 3 — the per-PR loop

Hand each PR (or a batch that touches the same files) to a subagent using
[`references/pr-agent-prompt.md`](references/pr-agent-prompt.md); fan-out mechanics are in
[`pr-backlog-triage`](../pr-backlog-triage/SKILL.md). Scale depth to risk: a docs or test-only
PR needs the read plus green CI; a behavior change needs its tests run in the worktree; an
installer, UI or init change needs a cold-state check. Whole-backlog sweeps that spend 20+
minutes per PR are too slow.

1. **Read everything**: diff, body, and every comment on all surfaces (issue comments, reviews,
   inline threads). Judge "addressed" against the live diff, not the PR's summary.
2. **Still needed?** Check `main` for the same change and the open list for a duplicate or a
   stack (`git merge-base --is-ancestor`). Merge stacked PRs bottom-up. A duplicate or
   superseded PR is closed with evidence and thanks, crediting the contributor. When a valuable
   fix goes quiet, prefer finishing it on their branch over closing it.
3. **Fix what's wrong on their branch.** Merge `origin/main` into it (never rebase or force-push),
   resolving additively. Push to the head repo (`gh api repos/amd/gaia/pulls/<N> --jq
   .head.repo.full_name`) only when `maintainerCanModify` is true; otherwise comment the
   requested change. Confirm `.head.sha` moved, then re-approve fork runs (Step 2). Keep their
   authorship; small catch-up commits are fine, rewriting their change is not. Run `git diff
   --check` before pushing: Windows CRLF has hidden conflict markers before.
4. **Root-cause red CI from the job log, not the job name.** Reproduces on bare `main`: not the
   PR's fault, wait for the fix or use `--auto`. Outages count as "never ran": a review bot out
   of quota, `API rate limit exceeded` for the GitHub App, a bad runner image, a docs-host 404.
   Re-run those in small batches (about 8, spaced a couple of minutes apart) instead of
   pushing every PR at once.
5. **Verify the change works.** Run its tests from the worktree (the root `conftest.py` pins
   this checkout). Anything touching `~/.gaia` or install/uninstall runs with `HOME` and
   `USERPROFILE` pointed at a temp dir. A fix ships with a test that fails without it plus
   adversarial tests that try to break it (bypass, false positive, boundary, race). A flake
   fix removes the race, not widens the timeout.
6. **Holds** (say the exact gap in one PR comment): an incomplete fix (a gate that approves
   wider than it showed, a half-closed leak); a security-sensitive permission or confirmation
   change, which the user signs off even when it is sound (a suspected vulnerability is never
   discussed publicly: follow [`github-issue-response`](../github-issue-response/SKILL.md)
   and [`security-assessment`](../security-assessment/SKILL.md)); a very large PR, which you
   take to green but don't auto-merge.
7. **LLM-affecting diffs** (system prompt, tool docstring/schema/registration, retry or
   grounding logic, error classification): the maintainer's current policy is that flagship
   evals run on RC and release tags, not per PR. Don't hold on it, but name the categories in
   the PR comment so they are re-run on the next RC, and report them to the user. If the user
   says to gate on evals, hold instead.
8. **Independent verifier.** A second agent that never touched the PR reviews the diff, every
   comment, the tests and conflicts, reports only, and does not push, comment or merge. Fix
   its notes, then have it re-check that change. Neither the fixer nor the verifier merges:
   the orchestrating session does, after the verifier says enqueue.
9. **Merge.** `gh pr merge <N> --squash`. "The merge strategy for main is set by the merge
   queue" means the PR *was queued*, not refused; a follow-up plain `gh pr merge <N>` answers
   "already queued". Then confirm the outcome (`state`, `mergedAt`, or GraphQL
   `mergeQueueEntry`): a queued merge can still fail and leave the PR open.
10. **Close the loop.** "Closes #N" must match reality ("Refs #N" for a partial fix); confirm
    the issue closed or say why not; thank the contributor. If a PR merged with an unaddressed
    finding, open a follow-up PR for it.

## Step 4 — issues from outside contributors

For each unanswered third-party issue in the worklist:

- **Already fixed on `main`?** Check the code, not the issue's age. Close with the merged PR or
  evidence and the release that has it.
- **Fix in flight?** Link the PR. If a maintainer PR and a contributor PR overlap, say which
  supersedes which. Never leave two open PRs on one bug.
- **Real and unfixed:** label it (priority, area, `bug`), set a milestone, say it's accepted.
  A small bug with a clear reproduction gets fixed in a PR that credits the reporter.
- **Not something GAIA will take:** decline kindly with the specific reason and the existing
  alternative. A disclosed affiliation is thanked, not penalised.
- **Good first issue** only if small, scoped, testable without special hardware, still present
  on `main`, and not already being fixed. Verify each against `main`; skip epics, security,
  design or tracking items, and assigned issues.

Bulk changes (labels, milestones, closes): apply each batch as it is confirmed, not at the
end; give every close a comment with its evidence; retry transient `gh issue edit` failures;
on Windows strip `\r` from names read from files. Milestones: bug fixes in the current patch
line, features in the next minor.

Wording, length caps and the security protocol: [`github-issue-response`](../github-issue-response/SKILL.md)
and CLAUDE.md "How You Communicate": outcome first in plain words, mechanics in a collapsed
`<details><summary>🔍 Technical details</summary>` block. No AI attribution anywhere.

## Release-driven sweeps

- Honour the freeze that is in effect *now*; ask the user which one if unsure. In a bug-fix
  freeze only fixes and the user's named exceptions merge; the rest stay open, with a milestone.
- Re-run the scanner at the start of every round and before tagging. New PRs land mid-triage,
  held PRs return with a new commit (re-read the diff; a plausible follow-up often fixes a
  different thing), and `main` can go red under the whole batch. If `main` is red, one agent
  fixes that in its own PR and the rest wait or use `--auto`; never fix main-wide breakage
  inside an unrelated PR.
- Before the release closes: every merged outside-contributor PR since the last release is in
  the release notes (credit by commit author, `git log <last-tag>..origin/main`, not only a
  PR search; earlier releases also credited team members), and every outside-contributor
  issue is fixed, answered or deliberately deferred with a reason.

### Cut a release candidate at these checkpoints

Triage is a loop of merge windows, and each window ends in an RC, not a pile of merged PRs
nobody has built. Cut one when (1) a window of verified bug fixes has landed (typically the
outside-contributor batch), (2) a release blocker on `main` was just fixed because the last
RC failed to publish, or (3) the shortlist is empty: this one is the final's candidate.

1. **Close the window.** Stop merging, let every PR already in the merge queue land or drop
   out, and note the `main` SHA you are about to test.
2. **Wait for CI to finish and go green on that exact commit** (`gh run list --commit <sha>`).
   Tagging while main's CI still runs is the mistake that burned an RC before.
3. **Dry-run the release train** on it: `gh workflow run release_train_dryrun.yml --ref main -f
   rc_tag=vX.Y.Z-rcN`, and fix forward on `main` until it passes.
4. **Tag the RC** per [`gaia-release`](../gaia-release/SKILL.md) (ask first unless RC tags are pre-authorized). Watch the publish run to the end; an RC isn't done
   until its release, installers and website entry exist.
5. **Report what is in it**: PRs since the previous RC, outside contributors first, so the user
   can test their fixes and the notes can credit them.

If the publish run fails, that cause is the next triage item: one fix PR on `main`, then back
to step 1 with the next RC number.

## What to tell the user

Open with the counts and the shortlist: merged, queued, held, and for each held PR one line
with its concrete reason (waiting on contributor / incomplete fix / sign-off needed / waiting
on `main`) and its URL. Outside contributors' items come first, by handle. Say what you
approved CI for, and what you did not and why.

## Mistakes this skill exists to prevent

- Merging on "CI is green" when the only checks that ran were label and bot checks, or when the
  last review comment came after the last commit.
- Approving a fork's workflow runs without reading the diff, or approving a commit other than the
  one you read.
- Pushing to the wrong remote (`origin` is `amd/gaia`; a fork branch needs the fork URL), or
  force-pushing a contributor's branch.
- Treating "merged" as done while the issue it claims to close is still open.
- Hardcoding issue numbers for "known flakes" so a new regression is waved through; read the log.
- Acting on a stale checkout, or blaming a PR for what `main` already breaks.
- Two sessions working the same PR; check ownership first.
