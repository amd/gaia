# Claude CI Workflows — Helper Guide

Quick reference to the Claude-powered GitHub Actions in `amd/gaia`. Five workflow files:
one reactive assistant, its shared runner, two scheduled reviewers, and an auth canary.

All links point to `main`:

| Workflow | File |
|----------|------|
| Claude AI Assistant (main) | [`.github/workflows/claude.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude.yml) |
| Reusable runner | [`.github/workflows/claude-run.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-run.yml) |
| Weekly Audit (static) | [`.github/workflows/claude-weekly-audit.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-weekly-audit.yml) |
| Weekly Doc Walkthrough (live) | [`.github/workflows/claude-weekly-doc-walkthrough.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-weekly-doc-walkthrough.yml) |
| Auth Canary | [`.github/workflows/claude-auth-canary.yml`](https://github.com/amd/gaia/blob/main/.github/workflows/claude-auth-canary.yml) |

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

## 3. `claude-weekly-audit.yml` — proactive static review (scheduled)

Weekly (deeper whole-codebase sweep monthly). Fans out one **read-only** Claude job per
dimension — **security, correctness (the "fail loudly" rule), docs, tests, features** — then
files one ranked triage issue + per-finding child issues. **Human-gated**: reports findings
only; a maintainer adds the `bug` label to hand one to the auto-fix job. Runs on Opus.

## 4. `claude-weekly-doc-walkthrough.yml` — live "act like a real user" (scheduled)

The audit only *reads* code; this one *runs* GAIA. On a self-hosted Windows/STX runner it walks
each doc guide's commands for real (fresh venv, isolated config, dedicated Lemonade port) to
catch cold-start bugs invisible to source review (import errors on a plain PyPI install, agents
falling back to an uninstalled model). Sonnet executes, Opus judges. Files its own parent issue.

## 5. `claude-auth-canary.yml` — monthly health check

All Claude jobs use an OAuth token that expires ~yearly and fails *silently* (jobs go green but
post nothing). Once a month this runs a trivial Haiku prompt; if auth is broken it opens a
tracking issue with fix steps — turning a silent multi-week outage into a notification.

---

## Key facts worth knowing

- **Auth:** all jobs prefer a subscription OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`) over a billed
  API key; they fall back to `ANTHROPIC_API_KEY` if the token is missing/expired. (The eval
  judge in `test_eval_rag.yml` still needs `ANTHROPIC_API_KEY` — OAuth tokens can't
  authenticate direct API/SDK calls.)
- **Fork safety:** fork-facing jobs run under `pull_request_target` (base-repo permissions).
  Safe because they only *read* code and *post* comments — **never execute PR code** (no
  `pip install` / `npm install` / build). Don't add steps that run checked-out code.
- **Modes:** jobs run in *automation* mode (`prompt` input), not tag mode (which has an
  upstream bug that silently ignores `--model`).
- **Shape overall:** `claude.yml` = reactive assistant → `claude-run.yml` = shared secure
  runner → two `weekly-*` = proactive coverage → canary = keeps it all from dying silently.
