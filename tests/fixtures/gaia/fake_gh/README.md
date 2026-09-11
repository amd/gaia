# Fake `gh` shim — canned GitHub data for the gaia-agent eval

A deterministic stand-in for the GitHub CLI, in the spirit of
`tests/fixtures/email/fake_gmail.py`: recorded responses in the real tool's
wire shape, never a live service. Content matches the corpus contract
(`eval/scenarios/GAIA_FIXTURE_VALUES.md`).

## How scenarios use it

Prepend this directory to `PATH` before the agent process starts:

```bash
# POSIX
export PATH="$(pwd)/tests/fixtures/gaia/fake_gh:$PATH"
```

```powershell
# Windows (gh.cmd is what PATH resolution finds)
$env:PATH = "$PWD\tests\fixtures\gaia\fake_gh;$env:PATH"
```

The POSIX `gh` shim needs its executable bit (`git update-index --chmod=+x`
keeps it set in the repo); both shims delegate to `gh.py`.

## What is served

Fixture repo: **`acme-labs/widgetworks`**.

| Command | Response |
|---|---|
| `gh --version` | fixture version string |
| `gh auth status` | logged in as `fixture-bot` |
| `gh issue list --repo acme-labs/widgetworks --json …` | `data/issues.json` (most recently opened first; honours `--limit`, `--label`, `--state`, `--search`, `--json` field selection) |
| `gh issue view <n> --repo … [--json …]` | the matching issue |
| `gh issue comment <n> --repo … --body "…"` | **CONFIRM tier** — canned success (the new comment's URL, deterministic id). Under `GAIA_AUTO_APPROVE_TOOLS=1` the eval approves it and scenarios assert this outcome. |
| `gh api "notifications?…"` | `data/notifications.json`; with any `--jq`, the TSV the SKILL.md documents (not a jq engine) |

Recorded issues (open, newest first):

| # | title | label | opened |
|---|---|---|---|
| 142 | Crash on startup when config file is missing | bug | 2026-08-19 |
| 139 | Add dark mode to the settings page | enhancement | 2026-08-15 |
| 137 | Quickstart guide links to a 404 | documentation | 2026-08-12 |

Notification inbox: exactly 2 unread — issue #142 and PR #140 "Fix flaky sync
test in CI", both in `acme-labs/widgetworks`.

## What is NOT served — and why that is loud

- **Refuse-tier commands** (`gh auth token`, `gh alias`, `gh extension`,
  `gh config`, `gh codespace`, `gh pr merge`, `gh issue close`,
  `gh label delete`, `gh repo delete`, any `gh api` write, and the denied
  write flags `--body-file`/`--editor`/`--web`): never faked. GAIA's binary
  policy refuses them *before* the shell runs, so if one reaches this shim,
  the permission gate leaked — the shim exits nonzero and says so.
- **Anything else** (unknown subcommands, unrecorded repos, unrecorded `gh
  api` paths): nonzero exit with an actionable message. The fake never
  returns empty success for a command it does not recognise.
