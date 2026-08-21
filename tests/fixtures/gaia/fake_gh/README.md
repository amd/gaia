# Fake `gh` shim — canned GitHub data for the gaia-agent eval

A deterministic stand-in for the GitHub CLI, in the spirit of
`tests/fixtures/email/fake_gmail.py`: recorded responses in the real tool's
wire shape, never a live service. The github-triage skill's scenarios run
against it byte-for-byte reproducibly.

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

## What is served (ALLOW tier only)

| Command | Response |
|---|---|
| `gh --version` | fixture version string |
| `gh auth status` | logged in as `fixture-bot` |
| `gh issue list --repo gaia-fixtures/widget-factory --json …` | `data/issues.json` (honours `--limit`, `--label`, `--state`, `--search`, `--json` field selection) |
| `gh issue view <n> --repo gaia-fixtures/widget-factory [--json …]` | the matching issue |
| `gh api "notifications?…"` | `data/notifications.json`; with any `--jq`, the TSV the SKILL.md documents (not a jq engine) |

The only recorded repository is **`gaia-fixtures/widget-factory`** (8 open
issues). Planted ground truth for judges:

- **#106** (data loss on network shares) is the most severe: data loss,
  affects everyone saving to a share, confirmed by the maintainer.
- **#101 + #102** describe the same underlying problem (crash when the config
  file is missing) — a correct triage clusters them.
- **#103** is a silent-wrong-answer bug (locale comma decimals) that outranks
  the cosmetic **#107** typo.
- The notification feed's top-priority entries are the `review_requested` PR
  and the `assign`ed issue; the `subscribed` release is skippable noise.

## What is NOT served — and why that is loud

- **Refuse-tier commands** (`gh auth token`, `gh alias`, `gh extension`,
  `gh config`, `gh codespace`, `gh pr merge`, `gh issue close`,
  `gh label delete`, `gh repo delete`, any `gh api` write): never faked. GAIA's
  binary policy refuses them *before* the shell runs, so if one reaches this
  shim, the permission gate leaked — the shim exits nonzero and says so.
- **Anything else** (unknown subcommands, unrecorded repos, unrecorded `gh api`
  paths): nonzero exit with an actionable message. The fake never returns empty
  success for a command it does not recognise.
