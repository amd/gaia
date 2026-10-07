# Claude CI Workflows — Helper Guide

Quick reference to the Claude-powered GitHub Actions in `amd/gaia`. Seven workflow files:
one reactive assistant, its shared runner, three scheduled reviewers, an auth canary, and an
account switch.

All links point to `main`:

| Workflow | File |
|----------|------|
| Claude AI Assistant (main) | [`.github/workflows/claude.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude.yml) |
| Reusable runner | [`.github/workflows/claude-run.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-run.yml) |
| Nightly Audit (static) | [`.github/workflows/claude-nightly-audit.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-nightly-audit.yml) |
| Security Audit (static) | [`.github/workflows/claude-security-audit.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-security-audit.yml) |
| Weekly Doc Walkthrough (live) | [`.github/workflows/claude-weekly-doc-walkthrough.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-weekly-doc-walkthrough.yml) |
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

## 3. `claude-nightly-audit.yml` — proactive static review (scheduled)

Nightly, reviewing the last day of merged work (whole-codebase sweep on Sundays). Fans out one
**read-only** Claude job per dimension — **correctness (the "fail loudly" rule), docs, tests,
features** — then files **one issue per defect**, labelled `weekly-audit`, deduplicated against
the whole open backlog. The run summary goes to the job summary, not an issue. **Human-gated**:
reports findings only; a maintainer adds the `bug` label to hand one to the auto-fix job.

## 4. `claude-security-audit.yml` — proactive security review (scheduled)

Nightly, security only. A semgrep pass over the whole tree plus a Claude taint/authz pass that
re-verifies every `# noqa: S*` / `# nosec`. Findings go to the private **Security → Code
scanning** tab, never a public issue; high-severity ones ping @kovtcharov-amd.

## 5. `claude-weekly-doc-walkthrough.yml` — live "act like a real user" (scheduled)

The audit only *reads* code; this one *runs* GAIA. On a self-hosted Windows/STX runner it walks
each doc guide's commands for real (fresh venv, isolated config, dedicated Lemonade port) to
catch cold-start bugs invisible to source review (import errors on a plain PyPI install, agents
falling back to an uninstalled model). Sonnet executes, Opus judges. Files its own parent issue.

## 6. `claude-auth-canary.yml` — monthly health check

All Claude jobs use an OAuth token that expires ~yearly and fails *silently* (jobs go green but
post nothing). Once a month this runs a trivial Haiku prompt; if auth is broken it opens a
tracking issue with fix steps — turning a silent multi-week outage into a notification.

## 7. `claude-account-switch.yml` — second subscription when the first runs out

Every Claude step reads `CLAUDE_CODE_OAUTH_TOKEN_SECONDARY` instead of
`CLAUDE_CODE_OAUTH_TOKEN` while the repo variable `CLAUDE_ACCOUNT` is `secondary`. Hourly,
this probes the primary account with the canary's probe:

- **Out of quota:** it checks the secondary account answers, sets `CLAUDE_ACCOUNT=secondary`,
  and opens "Claude CI is running on the secondary account".
- **Answering again** (the weekly limit reset): it sets `CLAUDE_ACCOUNT=primary` and closes
  that issue.
- **Bad credential:** it fails and switches nothing — rotate the token instead.

Setup, once:

```bash
# Signed in to the second account:
claude setup-token
gh secret set CLAUDE_CODE_OAUTH_TOKEN_SECONDARY --repo amd/gaia
# A fine-grained token with "Variables: write" on amd/gaia — GITHUB_TOKEN cannot set variables:
gh secret set CLAUDE_ACCOUNT_SWITCH_TOKEN --repo amd/gaia
```

Without `CLAUDE_ACCOUNT_SWITCH_TOKEN` the job still detects the outage and fails with the
command to run by hand. Force an account with
`gh workflow run claude-account-switch.yml -f account=secondary` (or `primary`), or set the
variable directly: `gh variable set CLAUDE_ACCOUNT --body secondary`. The switch can lag up to
an hour behind the primary running out; jobs in that window fail as they did before.

---

## Key facts worth knowing

- **Auth:** all jobs prefer a subscription OAuth token over a billed API key: the primary
  account's `CLAUDE_CODE_OAUTH_TOKEN`, or `CLAUDE_CODE_OAUTH_TOKEN_SECONDARY` while
  `CLAUDE_ACCOUNT=secondary` (see §7). They fall back to `ANTHROPIC_API_KEY` if the selected
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
  runner → nightly audit, security audit and doc walkthrough = proactive coverage → canary = keeps it all from dying silently → account switch = keeps it running when one
  subscription runs out.
