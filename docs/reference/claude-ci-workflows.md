# Claude CI Workflows — Helper Guide

Quick reference to the Claude-powered GitHub Actions in `amd/gaia`. Seven workflow files:
one reactive assistant, its shared runner, three proactive reviewers and the rotation that
schedules them, and an auth canary. Every one picks its Claude account at run time (§8).

All links point to `main`:

| Workflow | File |
|----------|------|
| Claude AI Assistant (main) | [`.github/workflows/claude.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude.yml) |
| Reusable runner | [`.github/workflows/claude-run.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-run.yml) |
| Audit Rotation (scheduler) | [`.github/workflows/claude-audit-rotation.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-audit-rotation.yml) |
| Code Audit (static) | [`.github/workflows/claude-nightly-audit.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-nightly-audit.yml) |
| Security Audit (static) | [`.github/workflows/claude-security-audit.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-security-audit.yml) |
| Doc Walkthrough (live) | [`.github/workflows/claude-weekly-doc-walkthrough.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-weekly-doc-walkthrough.yml) |
| Auth Canary | [`.github/workflows/claude-auth-canary.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-auth-canary.yml) |
| Account picker (composite action) | [`.github/actions/claude-credential/action.yml`](https://github.com/amd/gaia/blob/main/.github/actions/claude-credential/action.yml) |

Related config the workflows depend on:
[`REVIEW.md`](https://github.com/amd/gaia/blob/main/REVIEW.md) (the PR-review rubric) and the
"Issue Response Guidelines" section of [`CLAUDE.md`](https://github.com/amd/gaia/blob/main/CLAUDE.md)
(shared tone/format).

---

## 1. `claude.yml` — "Claude AI Assistant" (the main, reactive one)

Reacts to issue/PR activity. Six jobs:

| Job | Fires when | What it does |
|-----|-----------|--------------|
| **pr-review** | PR opened / reopened / marked ready | Full diff review (works on fork PRs too) |
| **pr-rereview** | New commits pushed to a PR (`synchronize`) | Lightweight re-check; flags *new* regressions and unfixed Request changes findings, silent otherwise |
| **issue-handler** | New issue opened, or `@claude` in an issue/PR comment | Conversational reply |
| **pr-comment** | Review comment left on a PR | Responds (non-fork PRs only — GitHub hides secrets from forks on this event) |
| **auto-fix** | Issue gets the `bug` label (or a bug issue is reopened), or an audit dispatches it for a new auto-fixable issue | Attempts the fix: creates branch, opens a PR (draft + manual test plan if it can't self-validate), comments on the issue with test steps |
| **release-notes** | "Publish Release" workflow succeeds on a `v*` tag | Generates GH release notes + docs, bumps version, updates `docs.json` |

Every review also searches for sibling paths on PRs of 200+ changed lines, applies the
known bug classes in `scripts/audit/missed_bug_classes.md` (base-branch copy), and checks
for documentation drift.

`pr-review-verdict.yml` turns the review into a commit status, `PR review verdict`. It fails
while the latest review says Request changes, and a reply does not clear it. It clears when a
push passes re-review or a maintainer adds the `review-override` label. It blocks a merge
only if branch protection requires that status.

## 2. `claude-run.yml` — reusable runner (not triggered directly)

Called by the three *conversational* jobs (pr-review, pr-comment, issue-handler). Centralizes
checkout + diff generation, **retries** the intermittent upstream install crash, and verifies
Claude produced output. Kept in a separate file for security: on fork PRs GitHub reads it from
trusted `main`, so a malicious PR can't tamper with the credential-holding step.

## 3. `claude-audit-rotation.yml` — one proactive audit a night

The only scheduler for the three proactive audits below. At 10:13 UTC (03:13 PDT / 02:13 PST)
it dispatches **one** of them, picked by `days since 1970-01-01 mod 3`, so each runs every
third night. Running all three every night spent the Claude subscription's weekly cap mid-week.
Adding an audit means appending it to `ROTATION` in `scripts/audit/audit_rotation.py`.

Each audit then scopes itself from its own last **successful** rotation run, so a night lost to
the usage limit is covered on that audit's next turn: the code audit reviews everything merged
since then, the walkthrough walks the guides that waited longest. Before any lens starts, each
audit probes Claude once and stops with the account and reset time in its summary if the probe
is refused, and writes `recent-changes.md` (what merged since its last run, with PR titles) for
the lenses to read. All three also read `scripts/audit/missed_bug_classes.md`, the bug classes
audits keep missing; refresh it monthly with `python scripts/audit/miss_analysis.py`.

Run any audit on demand with `gh workflow run <file>`; see what tonight would run with
`gh workflow run claude-audit-rotation.yml -f dry_run=true`.

## 4. `claude-nightly-audit.yml` — proactive static code review ("Claude Code Audit")

Every third night, reviewing everything merged since its last successful run, plus a
whole-codebase sweep when the last successful one is over 8 days old (every third turn). Fans
out one **read-only** Claude job per dimension — **correctness (the "fail loudly" rule), docs,
tests, features** — then files **one issue per defect**, labelled `weekly-audit`, deduplicated
against the whole open backlog. The run summary goes to the job summary, not an issue.
New issues marked auto-fixable go straight to the auto-fix job, up to 5 a run, and a human
reviews the PR it opens. Others wait for a maintainer to add the `bug` label.

## 5. `claude-security-audit.yml` — proactive security review

Every third night, security only. A semgrep pass (Python, TypeScript, Go and the workflows
themselves) plus a Claude taint/authz pass that re-verifies every `# noqa: S*` / `# nosec`.
Findings go to the private **Security → Code scanning** tab, never a public issue;
high-severity ones ping @kovtcharov-amd.

## 6. `claude-weekly-doc-walkthrough.yml` — live "act like a real user" ("Claude Doc Walkthrough")

The code audit only *reads* code; this one *runs* GAIA. Every third night, on a self-hosted
Windows Strix Point runner, it walks ten doc guides' commands for real (fresh venv, isolated
config, dedicated Lemonade port) to catch cold-start bugs invisible to source review. Guides a
recent merge changed go first, then the least recently walked, so every guide comes round about
every nine days. One Claude session executes, a separate one judges the transcript, and a
synthesis step files one issue per defect.

## 7. `claude-auth-canary.yml` — monthly health check

All Claude jobs use an OAuth token that expires ~yearly and fails *silently* (jobs go green but
post nothing). Once a month this runs a trivial prompt on **both** accounts; if either token is
rejected, or both are out of quota, it opens a tracking issue with fix steps — turning a silent
multi-week outage into a notification. Probing both matters because the failover in §8 would
otherwise hide a lapsed token behind the other account.

## 8. Two accounts, picked per job at run time

CI holds two subscription tokens: `CLAUDE_CODE_OAUTH_TOKEN` (primary) and
`CLAUDE_CODE_OAUTH_TOKEN_SECONDARY` (backup). Nothing is stored about which one is current. Each
job asks:

- **Primary answers:** the job uses it.
- **Primary out of quota:** the job uses the secondary if it answers, with a warning naming
  the primary's reset time.
- **Both out of quota:** the job fails before doing any work, naming both reset times.
- **A token is rejected:** the job fails naming the secret to rotate. A bad credential never
  moves a job to the other account, because that would hide it.

Moving back to the primary needs nothing: the next job probes it first.

"Out of quota" means the refusal says so ("You've hit your limit", "usage limit
reached", "weekly limit", a 402, and so on). The phrases live in one list,
[`src/gaia/eval/claude_refusal.py`](https://github.com/amd/gaia/blob/main/src/gaia/eval/claude_refusal.py),
which the picker, the eval runner and `claude-run.yml` all use; a unit test fails if
`claude-run.yml`'s copy drifts. A bare per-minute 429 (`rate_limit_error`) is throttling
on an account that still has quota, so it never moves a job to the other account.

How each workflow asks:

| Where | How |
|-------|-----|
| Single-job workflows (auto-fix, release notes, Lemonade bump, patch release, skill review, the evals) | The [`claude-credential`](https://github.com/amd/gaia/blob/main/.github/actions/claude-credential/action.yml) action probes with a one-word prompt and reports a label (`primary`, `secondary`, or `none` when neither token is set and `ANTHROPIC_API_KEY` is used). The workflow picks the secret by that label, so no token is written to an output. |
| The three audits | Their gate job runs the action once and exports the label; every lens and synthesis job reads it. |
| `claude-run.yml` (reviews, replies, triage) | Inline: it runs on the primary and, if refused with quota wording before reaching a model, once more on the secondary. A run that reached the model is never retried, because replies post through `gh` mid-run and could post twice. It cannot use the action, because it checks out fork code and a local action would be read from that checkout. |
| `gaia eval agent` (long eval lanes) | Starts on the probed account with the other as `CLAUDE_CODE_OAUTH_TOKEN_BACKUP`. If the first runs out mid-run, the runner switches once, re-runs the scenario that was cut off, and prints `[ACCOUNT SWITCH]`. |

**When both accounts run out mid-eval,** the runner stops starting scenarios and keeps what
finished. `scorecard.json` gets a `partial` block (`partial: N of M ran, stopped because both
Claude accounts are out of quota (...)`), and every unstarted scenario is `NOT_RUN`, never a
pass, fail or zero score. The collect, compare and upload steps still run, so the partial
scorecard is uploaded. `--compare` leaves `NOT_RUN` scenarios out of regressions and progress
and prints `[INCOMPLETE]`. The integrity gate fails the job whatever `enforce` says, so a
partial run is never read as a full one. Resume it with the `gaia eval agent --resume` command
the log prints.

Setup, once:

```bash
# Signed in to the second account:
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN_SECONDARY --repo amd/gaia
```

---

## Key facts worth knowing

- **Auth:** all jobs prefer a subscription OAuth token over a billed API key: the primary
  account's `CLAUDE_CODE_OAUTH_TOKEN`, or `CLAUDE_CODE_OAUTH_TOKEN_SECONDARY` when the primary
  is out of quota (see §8). They fall back to `ANTHROPIC_API_KEY` only when neither token is
  set. An OAuth token
  cannot authenticate a direct SDK call, so anything that judges through the Anthropic SDK has
  to route the token through the `claude` CLI instead — that is what
  `gaia.eval.judge_client` does for the email evals (drafting / action-item / briefing). A judge
  still wired straight to the SDK needs `ANTHROPIC_API_KEY`.
- **Fork safety:** fork-facing jobs run under `pull_request_target` (base-repo permissions).
  Safe because they only *read* code and *post* comments — **never execute PR code** (no
  `pip install` / `npm install` / build). Don't add steps that run checked-out code.
- **Modes:** jobs run in *automation* mode (`prompt` input), not tag mode (which has an
  upstream bug that silently ignores `--model`).
- **Shape overall:** `claude.yml` = reactive assistant → `claude-run.yml` = shared secure
  runner → audit rotation (code audit, security audit, doc walkthrough, one a night) =
  proactive coverage → canary = keeps it all from dying silently → per-job account pick = keeps
  it running when one subscription runs out.
