# Claude CI Workflows — Helper Guide

Quick reference to the Claude-powered GitHub Actions in `amd/gaia`. Eight workflow files:
one reactive assistant, its shared runner, three proactive reviewers and the rotation that
schedules them, an auth canary, and an account switch.

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
| Account Switch | [`.github/workflows/claude-account-switch.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-account-switch.yml) |

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
| **pr-rereview** | New commits pushed to a PR (`synchronize`) | Lightweight Sonnet re-check; flags only *new* regressions, silent otherwise |
| **issue-handler** | New issue opened, or `@claude` in an issue/PR comment | Conversational reply |
| **pr-comment** | Review comment left on a PR | Responds (non-fork PRs only — GitHub hides secrets from forks on this event) |
| **auto-fix** | Issue gets the `bug` label (or a bug issue is reopened) | Attempts the fix: creates branch, opens a PR (draft + manual test plan if it can't self-validate), comments on the issue with test steps |
| **release-notes** | "Publish Release" workflow succeeds on a `v*` tag | Generates GH release notes + docs, bumps version, updates `docs.json` |

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
**Human-gated**: reports findings only; a maintainer adds the `bug` label to hand one to the
auto-fix job.

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
post nothing). Once a month this runs a trivial Haiku prompt; if auth is broken it opens a
tracking issue with fix steps — turning a silent multi-week outage into a notification.

## 8. `claude-account-switch.yml` — second subscription when the first runs out

Every Claude step reads `CLAUDE_CODE_OAUTH_TOKEN_SECONDARY` instead of
`CLAUDE_CODE_OAUTH_TOKEN` while the repo variable `CLAUDE_ACCOUNT` is `secondary`. It runs
when Claude hits a wall, not on a clock: `claude.yml` dispatches it when a review, reply or
triage call is refused before reaching a model, and the code audit, security audit and doc
walkthrough dispatch it when their quota probe is refused. They dispatch it rather than rely on
its `workflow_run` hook because the rotation starts them with `GITHUB_TOKEN`, and GitHub does not
document that `workflow_run` fires for a run started that way. The hook still covers the auth
canary, hand-dispatched audits, and a lens refused after the probe passed. It probes the primary
account with the canary's probe:

- **Out of quota:** it checks the secondary account answers, then sets `CLAUDE_ACCOUNT=secondary`.
- **Answering again** (the weekly limit reset): it sets `CLAUDE_ACCOUNT=primary`.
- **Bad credential:** it fails and switches nothing — rotate the token instead.

Each run's summary shows the primary probe's verdict, when its limit resets, the plan, and
what actually happened. Every Claude job also posts a "Claude account" notice naming the account
it ran on — including when `CLAUDE_ACCOUNT` asks for the secondary but its secret is missing, so
the job fell back to the primary.

Setup, once:

```bash
# Signed in to the second account:
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN_SECONDARY --repo amd/gaia
# A fine-grained token with "Variables: write" on amd/gaia — GITHUB_TOKEN cannot set variables:
gh secret set CLAUDE_ACCOUNT_SWITCH_TOKEN --repo amd/gaia
```

Without `CLAUDE_ACCOUNT_SWITCH_TOKEN` the job still probes, and when a switch is needed it fails
once with the `gh variable set` command to run by hand. When no switch is needed it stays green.
Force an account with `gh workflow run claude-account-switch.yml -f account=secondary` (or
`primary`), or set the variable directly: `gh variable set CLAUDE_ACCOUNT --body secondary`.

Moving back to the primary happens only when a run's probe gets a clean answer from it. While CI
is on a working secondary, nothing triggers a run, so it stays there until a Claude job is refused
again or someone dispatches the workflow.

---

## Key facts worth knowing

- **Auth:** all jobs prefer a subscription OAuth token over a billed API key: the primary
  account's `CLAUDE_CODE_OAUTH_TOKEN`, or `CLAUDE_CODE_OAUTH_TOKEN_SECONDARY` while
  `CLAUDE_ACCOUNT=secondary` (see §8). They fall back to `ANTHROPIC_API_KEY` if the selected
  token is missing. An OAuth token
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
  proactive coverage → canary = keeps it all from dying silently → account switch = keeps it running when one
  subscription runs out.
